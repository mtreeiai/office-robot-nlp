from openai import OpenAI
import json

client = OpenAI()

SYSTEM_PROMPT = """
You are an intent parser for a household robot.

Available intents:

- MOVE_TO
- FETCH_OBJECT
- PUT_OBJECT
- FOLLOW_PERSON
- FIND_OBJECT
- STOP

Return ONLY valid JSON.

Schema:

{
    "intent": "...",
    "entities": {
        "object": null,
        "source_location": null,
        "destination": null,
        "color": null
    }
}
"""

def parse_intent(text):

    response = client.chat.completions.create(
        model="gpt-5.6",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": text
            }
        ]
    )

    return json.loads(
        response.choices[0].message.content
    )
    

