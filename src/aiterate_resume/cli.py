import argparse
import os
from pathlib import Path
from openai import OpenAI

from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from aiterate_resume.search_replace import (
    MultipleReplacementsError,
    NoReplacementError,
    execute_search_replace,
)
from .search_replace_format import (
    parse_search_replace_text,
    UnexpectedFenceError,
    UnexpectedEndOfInput,
)
from .system_prompts import system_prompt
from . import search_replace_prompts
from .chat import Console, ChatSession


def parse_args():
    # Set up argument parser
    arg_parser = argparse.ArgumentParser(description="Iterate on a resume with an AI.")
    arg_parser.add_argument("resume", type=str, help="Path to the file to be processed")
    arg_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    args = arg_parser.parse_args()

    # Read the contents of the specified file
    resume = Path(args.resume)
    with resume.open("r") as file:
        resume_contents = file.read()

    return args, resume_contents


def main():
    args, resume_contents = parse_args()
    console = Console(verbose=args.verbose)

    if "OPENAI_API_KEY" not in os.environ:
        console.expected_fatal_error(
            ValueError("OPENAI_API_KEY environment variable is not set")
        )

    session = ChatSession(OpenAI(api_key=os.environ["OPENAI_API_KEY"]), console)

    response = session.send_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": search_replace_prompts.format_prompt},
            *search_replace_prompts.examples,
            {
                "role": "user",
                "content": f"Provide changes to improve the following resume:\n\n{resume_contents}",
            },
        ]
    )

    changed_contents = resume_contents
    max_retries = 3
    parsed_suggestions = []
    for attempt in range(max_retries):
        raw_text = response.choices[0].message.content
        if not raw_text:
            raise ValueError("Got empty response.")

        try:
            parsed_suggestions = parse_search_replace_text(raw_text)
        except (UnexpectedFenceError, UnexpectedEndOfInput) as e:
            if attempt == max_retries - 1:
                console.expected_fatal_error(
                    RuntimeError(
                        f"Failed to parse suggestions after {max_retries} attempt: {e}"
                    )
                )

            response = session.send_messages(
                [
                    {
                        "role": "system",
                        "content": f"Your response was not in the correct *SEARCH/REPLACE block* format. Trying to parse it gave the error: {str(e)}. Please try again, ensuring your response follows the correct format.",
                    }
                ]
            )
            continue

        error_messages: list[ChatCompletionMessageParam] = []
        for suggestion in parsed_suggestions:
            try:
                changed_contents = execute_search_replace(suggestion, changed_contents)
            except (MultipleReplacementsError, NoReplacementError) as e:
                error_messages.append(
                    {
                        "role": "system",
                        "content": f"There was an error applying the following *SEARCH/REPLACE block* {e}\n\n{suggestion.to_block()}",
                    }
                )

        if error_messages:
            response = session.send_messages(error_messages)
            continue
        else:
            break
    else:
        console.expected_fatal_error(RuntimeError("Ran out of reflection attempts."))

    print(changed_contents)
