import asyncio
import json
import time
import logging
from typing import Any, Dict, ForwardRef, List, Optional, Type, Union

from fastapi import HTTPException

from mcp import ClientSession, types

logger = logging.getLogger(__name__)
from mcp.types import (
    CallToolResult,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)

from mcp.shared.exceptions import McpError

from pydantic import Field, create_model
from pydantic.fields import FieldInfo

# 专注于核心功能，移除性能优化组件

MCP_ERROR_TO_HTTP_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    METHOD_NOT_FOUND: 404,
    INVALID_PARAMS: 422,
    INTERNAL_ERROR: 500,
}


def process_tool_response(result: CallToolResult) -> list:
    """Universal response processor for all tool endpoints"""
    response = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            text = content.text
            if isinstance(text, str):
                try:
                    text = json.loads(text)
                except json.JSONDecodeError:
                    pass
            response.append(text)
        elif isinstance(content, types.ImageContent):
            image_data = f"data:{content.mimeType};base64,{content.data}"
            response.append(image_data)
        elif isinstance(content, types.EmbeddedResource):
            # TODO: Handle embedded resources
            response.append("Embedded resource not supported yet.")
    return response


def _process_schema_property(
    _model_cache: Dict[str, Type],
    prop_schema: Dict[str, Any],
    model_name_prefix: str,
    prop_name: str,
    is_required: bool,
    schema_defs: Optional[Dict] = None,
) -> tuple[Union[Type, List, ForwardRef, Any], FieldInfo]:
    """
    Recursively processes a schema property to determine its Python type hint
    and Pydantic Field definition.

    Returns:
        A tuple containing (python_type_hint, pydantic_field).
        The pydantic_field contains default value and description.
    """
    if "$ref" in prop_schema:
        ref = prop_schema["$ref"]
        ref = ref.split("/")[-1]
        assert ref in schema_defs, "Custom field not found"
        prop_schema = schema_defs[ref]

    prop_type = prop_schema.get("type")
    prop_desc = prop_schema.get("description", "")

    default_value = ... if is_required else prop_schema.get("default", None)
    pydantic_field = Field(default=default_value, description=prop_desc)

    # Handle the case where prop_type is missing but 'anyOf' key exists
    # In this case, use data type from 'anyOf' to determine the type hint
    if "anyOf" in prop_schema:
        type_hints = []
        for i, schema_option in enumerate(prop_schema["anyOf"]):
            type_hint, _ = _process_schema_property(
                _model_cache,
                schema_option,
                f"{model_name_prefix}_{prop_name}",
                f"choice_{i}",
                False,
            )
            type_hints.append(type_hint)
        return Union[tuple(type_hints)], pydantic_field

    # Handle the case where prop_type is a list of types, e.g. ['string', 'number']
    if isinstance(prop_type, list):
        # Create a Union of all the types
        type_hints = []
        for type_option in prop_type:
            # Create a temporary schema with the single type and process it
            temp_schema = dict(prop_schema)
            temp_schema["type"] = type_option
            type_hint, _ = _process_schema_property(
                _model_cache, temp_schema, model_name_prefix, prop_name, False
            )
            type_hints.append(type_hint)

        # Return a Union of all possible types
        return Union[tuple(type_hints)], pydantic_field

    if prop_type == "object":
        nested_properties = prop_schema.get("properties", {})
        nested_required = prop_schema.get("required", [])
        nested_fields = {}

        nested_model_name = f"{model_name_prefix}_{prop_name}_model".replace(
            "__", "_"
        ).rstrip("_")

        if nested_model_name in _model_cache:
            return _model_cache[nested_model_name], pydantic_field

        for name, schema in nested_properties.items():
            is_nested_required = name in nested_required
            nested_type_hint, nested_pydantic_field = _process_schema_property(
                _model_cache,
                schema,
                nested_model_name,
                name,
                is_nested_required,
                schema_defs,
            )

            nested_fields[name] = (nested_type_hint, nested_pydantic_field)

        if not nested_fields:
            return Dict[str, Any], pydantic_field

        NestedModel = create_model(nested_model_name, **nested_fields)
        _model_cache[nested_model_name] = NestedModel

        return NestedModel, pydantic_field

    elif prop_type == "array":
        items_schema = prop_schema.get("items")
        if not items_schema:
            # Default to list of anything if items schema is missing
            return List[Any], pydantic_field

        # Recursively determine the type of items in the array
        item_type_hint, _ = _process_schema_property(
            _model_cache,
            items_schema,
            f"{model_name_prefix}_{prop_name}",
            "item",
            False,  # Items aren't required at this level,
            schema_defs,
        )
        list_type_hint = List[item_type_hint]
        return list_type_hint, pydantic_field

    elif prop_type == "string":
        return str, pydantic_field
    elif prop_type == "integer":
        return int, pydantic_field
    elif prop_type == "boolean":
        return bool, pydantic_field
    elif prop_type == "number":
        return float, pydantic_field
    elif prop_type == "null":
        return None, pydantic_field
    else:
        return Any, pydantic_field


def get_model_fields(form_model_name, properties, required_fields, schema_defs=None):
    model_fields = {}

    _model_cache: Dict[str, Type] = {}

    for param_name, param_schema in properties.items():
        is_required = param_name in required_fields
        python_type_hint, pydantic_field_info = _process_schema_property(
            _model_cache,
            param_schema,
            form_model_name,
            param_name,
            is_required,
            schema_defs,
        )
        # Use the generated type hint and Field info
        model_fields[param_name] = (python_type_hint, pydantic_field_info)
    return model_fields


def get_tool_handler(
    session,
    endpoint_name,
    form_model_fields,
    response_model_fields=None,
    connection_name: str = "Unknown",
    connection_manager=None
):
    if form_model_fields:
        FormModel = create_model(f"{endpoint_name}_form_model", **form_model_fields)
        ResponseModel = (
            create_model(f"{endpoint_name}_response_model", **response_model_fields)
            if response_model_fields
            else Any
        )

        def make_endpoint_func(
            endpoint_name: str, FormModel, session: ClientSession
        ):  # Parameterized endpoint
            async def tool(form_data: FormModel) -> ResponseModel:
                args = form_data.model_dump(exclude_none=True)

                # 直接执行工具请求，专注于网络错误处理
                return await _execute_tool_request(
                    endpoint_name, args, session, connection_name, connection_manager
                )

            return tool

        tool_handler = make_endpoint_func(endpoint_name, FormModel, session)
    else:

        def make_endpoint_func_no_args(
            endpoint_name: str, session: ClientSession
        ):  # Parameterless endpoint
            async def tool():  # No parameters
                # 直接执行工具请求，专注于网络错误处理
                return await _execute_tool_request(
                    endpoint_name, {}, session, connection_name, connection_manager
                )

            return tool

        tool_handler = make_endpoint_func_no_args(endpoint_name, session)

    return tool_handler


async def _execute_tool_request(
    endpoint_name: str,
    args: Dict[str, Any],
    session: ClientSession,
    connection_name: str,
    connection_manager=None
) -> Any:
    """执行工具请求的核心逻辑，专注于网络错误处理和防卡死"""
    logger.debug(f"开始执行工具请求: {endpoint_name}, 连接: {connection_name}")

    # 使用传入的连接管理器，如果没有则导入全局的（避免循环导入）
    if connection_manager is None:
        from mcpo.main import connection_manager as global_connection_manager
        connection_manager = global_connection_manager
    from mcpo.utils.reconnect_manager import reconnect_manager, handle_connection_error

    max_retries = 3  # 最多重试3次
    base_timeout = 30.0  # 基础超时时间

    for attempt in range(max_retries + 1):
        try:
            # 快速验证会话是否可用
            if not session:
                logger.warning(f"会话为空，尝试获取新会话: {connection_name}")
                session = await reconnect_manager.get_healthy_session(connection_name)
                if not session:
                    raise HTTPException(
                        status_code=503,
                        detail={"message": "MCP服务器连接不可用", "error": "无法获取健康会话"}
                    )

            # 动态调整超时时间（重试时增加超时）
            current_timeout = base_timeout + (attempt * 10)  # 每次重试增加10秒

            logger.debug(f"调用工具 {endpoint_name} (尝试 {attempt + 1}/{max_retries + 1}, 超时: {current_timeout}s)")

            # 使用超时保护执行工具调用
            result = await asyncio.wait_for(
                session.call_tool(endpoint_name, arguments=args),
                timeout=current_timeout
            )

            # 检查工具执行结果
            if result.isError:
                error_message = "Unknown tool execution error"
                if result.content and isinstance(result.content[0], types.TextContent):
                    error_message = result.content[0].text

                # 记录工具执行错误
                logger.warning(f"工具执行错误 {endpoint_name}: {error_message}")
                connection_manager.record_connection_error(connection_name, error_message)

                # 对于工具执行错误，不进行重试，直接返回500
                raise HTTPException(
                    status_code=500,
                    detail={"message": error_message}
                )

            # 记录成功调用
            connection_manager.record_connection_success(connection_name)
            logger.debug(f"工具调用成功: {endpoint_name}")

            response_data = process_tool_response(result)
            return response_data[0] if len(response_data) == 1 else response_data

        except HTTPException:
            # HTTPException直接抛出，不重试
            raise
        except asyncio.TimeoutError:
            # 超时错误，记录并尝试重连
            timeout_error = f"工具调用超时 ({current_timeout}秒): {endpoint_name}"
            logger.warning(f"{timeout_error} (尝试 {attempt + 1}/{max_retries + 1})")
            connection_manager.record_connection_error(connection_name, timeout_error)

            if attempt < max_retries:
                logger.info(f"超时后尝试重连并重试: {endpoint_name}")
                # 尝试重连
                success = await reconnect_manager.attempt_reconnect(connection_name)
                if success:
                    session = await reconnect_manager.get_healthy_session(connection_name)
                    continue
                else:
                    logger.warning(f"重连失败: {connection_name}")

            # 最后一次尝试失败
            raise HTTPException(
                status_code=504,
                detail={"message": "工具调用超时", "error": timeout_error}
            )
        except Exception as e:
            error_str = str(e).lower()
            logger.warning(f"工具调用异常 {endpoint_name}: {str(e)} (尝试 {attempt + 1}/{max_retries + 1})")
            connection_manager.record_connection_error(connection_name, str(e))

            # 检查是否是网络相关错误
            network_errors = ["connection", "network", "timeout", "502", "503", "504", "524", "520", "521", "522", "523", "525"]
            is_network_error = any(keyword in error_str for keyword in network_errors)

            if is_network_error and attempt < max_retries:
                logger.info(f"检测到网络错误，尝试重连: {endpoint_name}")
                # 等待一段时间后重试
                await asyncio.sleep(min(2 ** attempt, 5))  # 指数退避，最大5秒

                # 尝试重连
                success = await reconnect_manager.attempt_reconnect(connection_name)
                if success:
                    session = await reconnect_manager.get_healthy_session(connection_name)
                    continue
                else:
                    logger.warning(f"重连失败: {connection_name}")

            # 最后一次尝试失败
            if attempt == max_retries:
                logger.error(f"工具调用 {endpoint_name} 最终失败: {str(e)}")

                if is_network_error:
                    raise HTTPException(
                        status_code=503,
                        detail={"message": f"MCP服务器连接问题: {str(e)}"}
                    )
                else:
                    raise HTTPException(
                        status_code=500,
                        detail={"message": f"工具执行失败: {str(e)}"}
                    )

    # 如果所有重试都失败，这里不应该到达
    raise HTTPException(
        status_code=500,
        detail={"message": "所有重试尝试都失败"}
    )


# 删除了复杂的辅助函数，保持代码简洁
