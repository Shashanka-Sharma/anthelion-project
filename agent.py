import os
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

load_dotenv()

APP_NAME = "anthelion_agent"
USER_ID = "user"
SESSION_ID = "session"


def get_current_time() -> dict:
    """Returns the current UTC time."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return {"current_time": now}


root_agent = Agent(
    name="anthelion_agent",
    model="gemini-2.5-flash",
    description="A helpful assistant agent for the Anthelion project.",
    instruction=(
        "You are a helpful AI assistant. "
        "Answer questions clearly and concisely. "
        "Use your available tools when they are relevant to the user's request."
    ),
    tools=[get_current_time],
)


async def run_agent(user_message: str) -> str:
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    content = types.Content(
        role="user",
        parts=[types.Part(text=user_message)],
    )

    final_response = ""
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=SESSION_ID,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_response = event.content.parts[0].text

    return final_response


if __name__ == "__main__":
    import asyncio

    async def main():
        print("Anthelion Agent — type 'quit' to exit.\n")
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ("quit", "exit"):
                break
            if not user_input:
                continue
            response = await run_agent(user_input)
            print(f"Agent: {response}\n")

    asyncio.run(main())
