import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.genai import types

from tools import get_company_info, get_fundamentals, get_news_headlines, get_price_history

load_dotenv()

APP_NAME = "anthelion_agent"
USER_ID = "demo_user"
SESSION_ID = f"session_{uuid.uuid4().hex[:8]}"

DEFAULT_TASK = (
    "Analyze Apple Inc. (AAPL) as an investment opportunity. "
    "Get the company background, review its full 2016 stock performance, check the fundamentals, "
    "and look up news headlines for 2-3 of the most notable trading days (biggest moves). "
    "Conclude with a buy/hold/sell recommendation as of April 2016, citing specific data."
)

root_agent = Agent(
    name="anthelion_market_agent",
    model="gemini-2.5-flash",
    description="Financial analysis agent with access to NYSE market data and news.",
    instruction=(
        "You are a financial analysis agent with access to NYSE stock data and news headlines. "
        "Available tools: get_company_info, get_price_history (2016 data only), "
        "get_fundamentals (annual, up to 2017), get_news_headlines (2003-2021). "
        "When analysing a stock: (1) call get_company_info for background, "
        "(2) call get_price_history for the relevant period, "
        "(3) call get_fundamentals for financial health, "
        "(4) call get_news_headlines for dates with notable price movements. "
        "Always cite specific numbers — dates, prices, percentages, financial figures. "
        "Structure your response with clear sections: Company Overview, Price Performance, "
        "Financial Health, News & Market Context, and Investment Perspective."
    ),
    tools=[get_company_info, get_price_history, get_fundamentals, get_news_headlines],
)


async def run_agent(task: str) -> str:
    db_url = os.environ.get("DATABASE_URL_ASYNC")
    session_service = (
        DatabaseSessionService(db_url=db_url) if db_url else InMemorySessionService()
    )

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )

    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
    content = types.Content(role="user", parts=[types.Part(text=task)])

    final_response = ""
    async for event in runner.run_async(
        user_id=USER_ID, session_id=SESSION_ID, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_response = event.content.parts[0].text

    return final_response


if __name__ == "__main__":
    task = (
        os.environ.get("DEMO_TASK")
        or (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK)
    )
    print(f"\nTask: {task}\n{'─' * 60}")
    result = asyncio.run(run_agent(task))
    print(f"\nAgent Response:\n{'─' * 60}\n{result}\n")
