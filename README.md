# ⚡️ mcpo

Expose any MCP tool as an OpenAPI-compatible HTTP server—instantly.

mcpo is a dead-simple proxy that takes an MCP server command and makes it accessible via standard RESTful OpenAPI, so your tools "just work" with LLM agents and apps expecting OpenAPI servers.

No custom protocol. No glue code. No hassle.

## 🤔 Why Use mcpo Instead of Native MCP?

MCP servers usually speak over raw stdio, which is:

- 🔓 Inherently insecure
- ❌ Incompatible with most tools
- 🧩 Missing standard features like docs, auth, error handling, etc.

mcpo solves all of that:

- ✅ Works with OpenAPI tools, SDKs, and UIs
- 🛡 Adds security and stability using web standards
- 🧠 Auto-generates interactive docs for every tool
- 🔌 Uses pure HTTP—no sockets, no glue code
- 🔄 Supports multiple MCP servers simultaneously
- 📊 Includes health checks and basic monitoring

## 🔄 Key Differences from Official MCP

This fork includes several improvements over the original:

- **Multi-server support**: Run multiple MCP servers concurrently without conflicts
- **Automatic reconnection**: Handles connection drops and 524 errors automatically
- **Docker support**: Ready-to-use Dockerfile and Docker Compose examples
- **Enhanced stability**: Better error handling and connection management

## 🚀 Quick Usage

We recommend using uv for lightning-fast startup and zero config.

```bash
uvx mcpo --port 8000 --api-key "top-secret" -- your_mcp_server_command
```

Or, if you’re using Python:

```bash
pip install mcpo
mcpo --port 8000 --api-key "top-secret" -- your_mcp_server_command
```

To use an SSE-compatible MCP server, simply specify the server type and endpoint:

```bash
mcpo --port 8000 --api-key "top-secret" --server-type "sse" -- http://127.0.0.1:8001/sse
```

To use a Streamable HTTP-compatible MCP server, specify the server type and endpoint:

```bash
mcpo --port 8000 --api-key "top-secret" --server-type "streamable_http" -- http://127.0.0.1:8002/mcp
```

To add custom headers for authentication or other purposes with SSE/Streamable HTTP servers:

```bash
# Single header
mcpo --port 8000 --server-type "sse" --header "Authorization: Bearer your-token" -- http://127.0.0.1:8001/sse

# Multiple headers
mcpo --port 8000 --server-type "streamable_http" \
  --header "Authorization: Bearer your-token" \
  --header "X-API-Key: your-api-key" \
  --header "User-Agent: mcpo/0.0.14" \
  -- http://127.0.0.1:8002/mcp
```

You can also run mcpo via Docker with no installation:

```bash
docker run -p 8000:8000 ghcr.io/open-webui/mcpo:main --api-key "top-secret" -- your_mcp_server_command
```

### 🐳 Docker Compose Usage

For production deployments or when using configuration files, Docker Compose provides a more convenient approach:

1. **Clone and build the project:**
   ```bash
   git clone https://github.com/open-webui/mcpo.git
   cd mcpo
   sudo docker build -t mcpo .
   ```

2. **Create environment file:**
   ```bash
   # Create .env file with your API key
   echo "API_KEY=your-secret-api-key" > .env
   ```

3. **Create docker-compose.yml:**
   ```yaml
   services:
     mcpo:
       image: mcpo
       container_name: mcpo
       restart: unless-stopped
       command: ["--config", "/app/config.json", "--api-key", "${API_KEY}"]
       ports:
         - 8000:8000
       volumes:
         - ./config.json:/app/config.json
       environment:
         - API_KEY=${API_KEY}
   ```

4. **Create your config.json file** (see configuration examples below)

5. **Start the service:**
   ```bash
   docker-compose up -d
   ```

Your mcpo server will be available at http://localhost:8000 with automatic restarts and persistent configuration.

Example:

```bash
uvx mcpo --port 8000 --api-key "top-secret" -- uvx mcp-server-time --local-timezone=America/New_York
```

That’s it. Your MCP tool is now available at http://localhost:8000 with a generated OpenAPI schema — test it live at [http://localhost:8000/docs](http://localhost:8000/docs).

🤝 **To integrate with Open WebUI after launching the server, check our [docs](https://docs.openwebui.com/openapi-servers/open-webui/).**

### 🔄 Using a Config File

You can serve multiple MCP tools via a single config file that follows the [Claude Desktop](https://modelcontextprotocol.io/quickstart/user) format:

Start via:

```bash
mcpo --config /path/to/config.json
```

Example config.json:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"]
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=America/New_York"]
    },
    "mcp_sse": {
      "type": "sse", // Explicitly define type
      "url": "http://127.0.0.1:8001/sse"
    },
    "mcp_streamable_http": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8002/mcp"
    }, // Streamable HTTP MCP Server
    "mcp_sse_with_auth": {
      "type": "sse",
      "url": "http://127.0.0.1:8003/sse",
      "headers": {
        "Authorization": "Bearer your-token",
        "X-API-Key": "your-api-key"
      }
    },
    "mcp_streamable_http_with_auth": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8004/mcp",
      "headers": {
        "Authorization": "Bearer your-token",
        "User-Agent": "mcpo/0.0.14"
      }
    }
  }
}
```

Each tool will be accessible under its own unique route, e.g.:
- http://localhost:8000/memory
- http://localhost:8000/time

Each with a dedicated OpenAPI schema and proxy handler. Access full schema UI at: `http://localhost:8000/<tool>/docs`  (e.g. /memory/docs, /time/docs)

**Multi-Server Support**: All configured servers run independently with isolated connection management. If one server encounters issues, others continue operating normally.

## 🔧 Requirements

- Python 3.8+
- uv (optional, but highly recommended for performance + packaging)

## 🛠️ Development & Testing

To contribute or run tests locally:

1.  **Set up the environment:**
    ```bash
    # Clone the repository
    git clone https://github.com/open-webui/mcpo.git
    cd mcpo

    # Install dependencies (including dev dependencies)
    uv sync --dev
    ```

2.  **Run tests:**
    ```bash
    uv run pytest
    ```

3.  **Running Locally with Active Changes:**

    To run `mcpo` with your local modifications from a specific branch (e.g., `my-feature-branch`):

    ```bash
    # Ensure you are on your development branch
    git checkout my-feature-branch

    # Make your code changes in the src/mcpo directory or elsewhere

    # Run mcpo using uv, which will use your local, modified code
    # This command starts mcpo on port 8000 and proxies your_mcp_server_command
    uv run mcpo --port 8000 -- your_mcp_server_command

    # Example with a test MCP server (like mcp-server-time):
    # uv run mcpo --port 8000 -- uvx mcp-server-time --local-timezone=America/New_York
    ```
    This allows you to test your changes interactively before committing or creating a pull request. Access your locally running `mcpo` instance at `http://localhost:8000` and the auto-generated docs at `http://localhost:8000/docs`.


## 🪪 License

MIT

## 🤝 Contributing

We welcome and strongly encourage contributions from the community!

Whether you're fixing a bug, adding features, improving documentation, or just sharing ideas—your input is incredibly valuable and helps make mcpo better for everyone.

Getting started is easy:

- Fork the repo
- Create a new branch
- Make your changes
- Open a pull request

Not sure where to start? Feel free to open an issue or ask a question—we’re happy to help you find a good first task.

## ✨ Star History

<a href="https://star-history.com/#open-webui/mcpo&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
  </picture>
</a>

---

✨ Let's build the future of interoperable AI tooling together!