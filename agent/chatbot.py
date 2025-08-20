import asyncio
import uuid
from typing import Any, List
import os

from langchain.chat_models import init_chat_model
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import (
    AIMessage, 
    HumanMessage, 
    AnyMessage, 
    SystemMessage)
from langgraph.graph import (
    StateGraph, 
    MessagesState, 
    START, 
    END)
from langgraph.prebuilt import (
    create_react_agent, 
    ToolNode, 
    tools_condition
)

from dotenv import load_dotenv
load_dotenv()

# Initialize model
model = init_chat_model(model="bedrock_converse:anthropic.claude-3-5-sonnet-20240620-v1:0")
parking_url = os.getenv("PARKING_MCP_URL", "http://localhost:9001/mcp")
checkpointer = InMemorySaver()

async def create_graph(checkpointer):
    """Main function to process queries using the MCP client."""
    client = MultiServerMCPClient({
        "parking": {
            "url": parking_url,
            "transport": "streamable_http"
        }
    })
    tools = await client.get_tools()
    print(tools)
    sys_prompt = """## 🎯 角色與任務 (Role & Permission)
你是一位專業又幽默的停車場搜尋助理：「停車寶 ϞϞ(๑⚈ ․̫ ⚈๑)∩」。  
你的目標是協助使用者快速找到指定地區附近的停車場，並提供：
- 停車場基本資訊（名稱、地址、營業時間、收費標準）
- 即時可停車位數
- Google Maps 導航連結

你可以透過 MCP Server 工具查詢資料，但**必須**先取得「經緯度」與「縣市名稱」。  
如果使用者沒有提供，請引導他用 LINE 分享位置。  

目前僅支援以下地區：
- Taipei
- NewTaipei

---

## 📋 任務流程（Processing）
1. 與使用者確認搜尋地點（請使用者直接透過 line 分享位置的功能分享）。
2. 使用 MCP Server 工具搜尋停車場資訊。
3. 根據結果與使用者需求，整理以下內容回覆：
   - 停車場名稱、地址
   - 收費方式
   - 營業時間
   - 即時剩餘車位數
   - Google Maps 導航連結（必須提供）
4. 回覆時使用親切、有禮貌的語氣，並可加入適量 Emoji（例如 🚗、🅿️ 等）。
5. 回覆不要過於冗長，有表達清楚即可。

---

## 🚫 禁忌內容（Don't）
- 禁止涉及腥羶色、仇恨言論
- 禁止涉及政治、宗教、種族、性別、性取向等敏感議題
- 禁止涉及暴力、血腥、恐怖、色情等內容

---

## 💬 回覆格式（Response Format）
- 語言：繁體中文（不得使用簡體中文）
- 風格：簡潔、必要資訊為主，避免冗長
- 停車資訊格式範例：
    ```
    🅿️ 停車場名稱
    🚗 剩餘車位：xx
    💰 費率：xx元/小時
    🕒 營業時間：xx:xx - xx:xx
    📍 導航：<Google Maps 連結>
    ```

---

## 🔗 Google Maps 導航連結生成
使用以下格式：https://www.google.com/maps/dir/?api=1&origin=<起點>&destination=<終點>&travelmode=driving
- `<起點>` 可用使用者當前位置（如果使用者有提供）
- `<終點>` 為停車場地址或經緯度
- **所有停車場都必須提供這個連結**

    """
    
    def __call_llm(state: MessagesState):
        state["messages"] = filter_conversation(state["messages"])
        messages = llm_with_tool.invoke([SystemMessage(content=sys_prompt)] + state["messages"])
        assert len(messages.tool_calls) <= 1
        
        return {"messages": [messages]}
    
    llm_with_tool = model.bind_tools(tools)
    graph_builder = StateGraph(state_schema=MessagesState)
    tool_node = ToolNode(tools)
    
    graph_builder.add_node("call_llm", __call_llm)
    graph_builder.add_node("tools", tool_node)
    
    graph_builder.add_edge(START, "call_llm")
    graph_builder.add_conditional_edges("call_llm", tools_condition)
    graph_builder.add_edge("tools", "call_llm")    
    
    agent = graph_builder.compile(checkpointer=checkpointer)
    
    # agent = create_react_agent(model=model, 
    #                            tools=tools, 
    #                            prompt=sys_prompt,
    #                            checkpointer=checkpointer)
    
    return agent


def filter_conversation(messages: List[AnyMessage]):
    def _is_conversation_turn_end(
        current_msg: AnyMessage, messages: List[AnyMessage], current_index: int
    ) -> bool:
        
        if not isinstance(current_msg, AIMessage) or current_msg.tool_calls:
            return False
        
        next_index = current_index + 1
        if next_index >= len(messages):
            return False
        
        next_msg = messages[next_index]
        
        return isinstance(next_msg, HumanMessage) and not next_msg.additional_kwargs.get("is_reflect", False)
    
    result = []
    current_chunk = []
    
    for current_index, current_msg in enumerate(messages):
        current_chunk.append(current_msg)
        if _is_conversation_turn_end(current_msg, messages, current_index):
            result.extend([current_chunk[0], current_chunk[-1]])
            current_chunk = []
            
    if current_chunk:
        result.extend(current_chunk)
    
    return result


async def call_agent(agent, user_id: str, query: str):
    config = {"configurable": {"thread_id": user_id}}
    response = await agent.ainvoke({"messages": query}, config=config)
    return response


if __name__ == "__main__":
    agent = asyncio.run(create_graph(checkpointer))

    user_id = str(uuid.uuid4())
    response = asyncio.run(call_agent(agent = agent, user_id=user_id, query="hi 我是 benson"))
    print(response['messages'][-1].content)
    response = asyncio.run(call_agent(agent = agent, user_id=user_id, query=f"你還記得我是誰嗎"))
    print(response['messages'][-1].content)