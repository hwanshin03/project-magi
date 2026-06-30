import os

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

api_key = os.getenv("ANTHROPIC_API_KEY")
print(api_key)

client = Anthropic(api_key=api_key)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=300,
    messages=[
        {
            "role": "user",
            "content": "What is artificial intelligence?"
        }
    ]
)

print(message.content[0].text)