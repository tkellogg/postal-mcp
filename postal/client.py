
import asyncio
import click
from fastmcp import Client

async def main(agent_name: str, action: str, recipient: str = None, message: str = None, port: int=7777):
    config = {
        "postal": {
            "url": f"http://localhost:{port}/agents/{agent_name}/mcp/"
        }
    }

    client = Client(config)

    async with client:
        if action == "send":
            if recipient is None:
                raise ValueError("Recipient must be specified when sending a message.")
            result = await client.call_tool("send_to_agent", {"name": recipient, "msg": message})
            print(f"Message sent: {result}")
        elif action == "check":
            print("Checking for mail...")
            result = await client.call_tool("check_mail", {})
            print(f"Message received: {result}")

@click.command()
@click.argument("agent_name")
@click.argument("action", type=click.Choice(["send", "check"]))
@click.option("--recipient", default=None)
@click.option("--message", default=None)
@click.option("--port", default=7777)
def run(agent_name: str, action: str, recipient: str, message: str, port: int):
    asyncio.run(main(agent_name, action, recipient, message, port))

if __name__ == "__main__":
    run()
