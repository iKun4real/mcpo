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

# 导入性能优化组件
from .cache import cache_manager, should_cache_response, get_cache_ttl
from .performance import (
    concurrency_limiter,
    request_deduplicator,
    performance_monitor
)

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

                # 性能监控
                async with performance_monitor.monitor_request(endpoint_name):
                    # 并发控制
                    async with concurrency_limiter.acquire():
                        # 检查缓存
                        cache = cache_manager.get_cache()
                        cached_result = await cache.get(endpoint_name, args)
                        if cached_result is not None:
                            logging.debug(f"缓存命中: {endpoint_name}")
                            return cached_result

                        # 请求去重
                        async def execute_request():
                            return await _execute_tool_request(
                                endpoint_name, args, session, connection_name, connection_manager
                            )

                        result = await request_deduplicator.execute_or_wait(
                            endpoint_name, args, execute_request
                        )

                        # 缓存结果
                        if should_cache_response(endpoint_name, args, result):
                            ttl = get_cache_ttl(endpoint_name, args)
                            await cache.set(endpoint_name, args, result, ttl)

                        return result

            return tool

        tool_handler = make_endpoint_func(endpoint_name, FormModel, session)
    else:

        def make_endpoint_func_no_args(
            endpoint_name: str, session: ClientSession
        ):  # Parameterless endpoint
            async def tool():  # No parameters
                # 性能监控
                async with performance_monitor.monitor_request(endpoint_name):
                    # 并发控制
                    async with concurrency_limiter.acquire():
                        # 检查缓存
                        cache = cache_manager.get_cache()
                        cached_result = await cache.get(endpoint_name, {})
                        if cached_result is not None:
                            logging.debug(f"缓存命中: {endpoint_name}")
                            return cached_result

                        # 请求去重
                        async def execute_request():
                            return await _execute_tool_request(
                                endpoint_name, {}, session, connection_name, connection_manager
                            )

                        result = await request_deduplicator.execute_or_wait(
                            endpoint_name, {}, execute_request
                        )

                        # 缓存结果
                        if should_cache_response(endpoint_name, {}, result):
                            ttl = get_cache_ttl(endpoint_name, {})
                            await cache.set(endpoint_name, {}, result, ttl)

                        return result

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
    """执行工具请求的核心逻辑，支持自动重连和会话状态同步"""
    logger.debug(f"开始执行工具请求: {endpoint_name}, 连接: {connection_name}")

    # 使用传入的连接管理器，如果没有则导入全局的（避免循环导入）
    if connection_manager is None:
        from mcpo.main import connection_manager as global_connection_manager
        connection_manager = global_connection_manager
    from mcpo.utils.reconnect_manager import reconnect_manager, handle_connection_error

    max_retries = 3  # 最多重试3次
    original_session = session  # 保存原始会话引用

    for attempt in range(max_retries + 1):
        try:
            # 获取当前最新的健康会话
            current_session = await _get_current_healthy_session(
                session, connection_name, reconnect_manager, connection_manager
            )

            if not current_session:
                error_msg = f"无法获取健康的会话连接: {connection_name}"
                logger.error(error_msg)
                connection_manager.record_connection_error(connection_name, error_msg)
                raise HTTPException(
                    status_code=503,
                    detail={"message": "MCP服务器连接不可用", "error": error_msg}
                )

            # 更新会话引用
            session = current_session

            # 添加超时机制防止长时间卡住
            logger.debug(f"调用工具 {endpoint_name} (尝试 {attempt + 1}/{max_retries + 1})")
            result = await asyncio.wait_for(
                session.call_tool(endpoint_name, arguments=args),
                timeout=30.0  # 30秒超时
            )

            # 检查工具执行结果
            if result.isError:
                error_message = "Unknown tool execution error"
                error_data = None

                if result.content:
                    if isinstance(result.content[0], types.TextContent):
                        error_message = result.content[0].text
                        try:
                            # 尝试解析错误数据
                            error_data = json.loads(error_message) if error_message.startswith('{') else None
                        except (json.JSONDecodeError, AttributeError):
                            pass

                detail = {"message": error_message}
                if error_data is not None:
                    detail["data"] = error_data

                # 记录工具执行错误
                logger.warning(f"工具执行错误 {endpoint_name}: {error_message}")
                connection_manager.record_connection_error(connection_name, error_message)

                # 对于工具执行错误，不进行重试，直接返回500
                raise HTTPException(
                    status_code=500,
                    detail=detail,
                )

            # 记录成功调用
            connection_manager.record_connection_success(connection_name)
            logger.debug(f"工具调用成功: {endpoint_name}")

            response_data = process_tool_response(result)
            final_response = (
                response_data[0] if len(response_data) == 1 else response_data
            )
            return final_response

        except HTTPException:
            # HTTPException直接抛出，不重试
            raise
        except asyncio.TimeoutError as e:
            # 超时错误，记录并尝试重连
            timeout_error = f"工具调用超时 (30秒): {endpoint_name}"
            logger.warning(f"{timeout_error} (尝试 {attempt + 1}/{max_retries + 1})")
            connection_manager.record_connection_error(connection_name, timeout_error)

            # 尝试处理超时错误并重连
            error_handled = await handle_connection_error(connection_name, Exception(timeout_error))

            if error_handled and attempt < max_retries:
                logger.info(f"超时后重连成功，重试工具调用 {endpoint_name} (尝试 {attempt + 2}/{max_retries + 1})")
                # 强制刷新会话状态
                await _refresh_session_state(connection_name, reconnect_manager)
                continue

            # 如果重连失败或达到最大重试次数
            if attempt == max_retries:
                raise HTTPException(
                    status_code=504,
                    detail={"message": "工具调用超时", "error": timeout_error}
                )
        except Exception as e:
            error_str = str(e).lower()
            logger.warning(f"工具调用异常 {endpoint_name}: {str(e)} (尝试 {attempt + 1}/{max_retries + 1})")

            # 特殊处理524错误：直接重试请求，不需要重连
            if "524" in error_str and attempt < max_retries:
                logger.warning(f"检测到524错误，直接重试请求 {endpoint_name} (尝试 {attempt + 2}/{max_retries + 1})")
                connection_manager.record_connection_error(connection_name, f"524错误重试: {str(e)}")
                await asyncio.sleep(min(2 ** attempt, 5))  # 指数退避，最大5秒
                continue

            # 对于其他错误，尝试处理连接错误并重连
            error_handled = await handle_connection_error(connection_name, e)

            if error_handled and attempt < max_retries:
                # 如果错误被处理（重连成功）且还有重试机会，则重试
                logger.info(f"重连成功，重试工具调用 {endpoint_name} (尝试 {attempt + 2}/{max_retries + 1})")
                # 强制刷新会话状态
                await _refresh_session_state(connection_name, reconnect_manager)
                continue

            # 如果无法处理错误或已达到最大重试次数，则抛出异常
            if attempt == max_retries:
                # 最后一次尝试失败，抛出原始异常
                logger.error(f"工具调用 {endpoint_name} 最终失败: {str(e)}")
                connection_manager.record_connection_error(connection_name, str(e))

                # 根据错误类型返回适当的HTTP状态码
                if any(keyword in error_str for keyword in ["connection", "network", "timeout", "502", "503", "504", "520", "521", "522", "523", "524", "525"]):
                    status_code = 503
                    detail_message = f"MCP服务器连接问题: {str(e)}"
                else:
                    status_code = 500
                    detail_message = "工具执行失败"

                raise HTTPException(
                    status_code=status_code,
                    detail={"message": detail_message, "error": str(e)},
                )

    # 如果所有重试都失败，这里不应该到达
    raise HTTPException(
        status_code=500,
        detail={"message": "未知错误", "error": "所有重试尝试都失败"}
    )


async def _get_current_healthy_session(
    session: ClientSession,
    connection_name: str,
    reconnect_manager,
    connection_manager
) -> Optional[ClientSession]:
    """获取当前健康的会话，如果当前会话不健康则尝试获取新的"""
    try:
        # 首先检查当前会话是否健康
        if session:
            try:
                # 快速健康检查
                await asyncio.wait_for(session.list_tools(), timeout=5.0)
                return session
            except Exception as e:
                logger.warning(f"当前会话不健康: {str(e)}")
                connection_manager.record_connection_error(connection_name, f"会话健康检查失败: {str(e)}")

        # 尝试从重连管理器获取健康会话
        healthy_session = await reconnect_manager.get_healthy_session(connection_name)
        if healthy_session:
            logger.info(f"获取到健康会话: {connection_name}")
            return healthy_session

        logger.error(f"无法获取健康会话: {connection_name}")
        return None

    except Exception as e:
        logger.error(f"获取健康会话时发生异常: {str(e)}")
        return None


async def _refresh_session_state(connection_name: str, reconnect_manager):
    """刷新会话状态，确保使用最新的连接"""
    try:
        # 强制重新获取会话状态
        await reconnect_manager.refresh_connection_state(connection_name)
        logger.debug(f"已刷新会话状态: {connection_name}")
    except Exception as e:
        logger.warning(f"刷新会话状态失败: {str(e)}")


async def _validate_session_health(session: ClientSession, timeout: float = 5.0) -> bool:
    """验证会话健康状态"""
    try:
        if not session:
            return False

        # 执行简单的健康检查
        await asyncio.wait_for(session.list_tools(), timeout=timeout)
        return True
    except Exception:
        return False
