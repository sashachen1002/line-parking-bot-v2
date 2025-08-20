import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from chatbot import create_graph, call_agent
from chatbot import checkpointer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    app.state.agent = await create_graph(checkpointer)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
async def health():
    return {"status": "200"}

@app.get("/chat")
async def chat(user_id: str, query: str):
    agent = getattr(app.state, "agent", None)
    if not agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    response = await call_agent(agent, user_id, query)
    response = response['messages'][-1].content
    
    return response
    
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)