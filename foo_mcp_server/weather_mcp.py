import asyncio
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP(name="Weather")

@mcp.tool()
async def get_weather(location: str) -> str:
    """Get weather for location."""
    return f"{location} 出現七道彩虹"

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({"status": "200"})

async def main():
    await mcp.run_async(transport="http", host='127.0.0.1', port=9000)

if __name__ == "__main__":
    asyncio.run(main())