import os

from dotenv import load_dotenv
from google import genai


def main():
    load_dotenv()

    client = genai.Client(
        api_key=os.getenv("GEMINI_API_KEY")
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Say hello to Hwan in one sentence."
    )

    print(response.text)


if __name__ == "__main__":
    main()
