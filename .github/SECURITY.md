# Security

## Scope

Untitled Project is built for **trusted use**: one person or a few people on their own machines, with their own OpenAI key and their own GPU server. The web player has **no authentication**, the LTX-2.5 server only an optional shared key, and both listen on `127.0.0.1` by default. Running either on a public network without adding authentication, HTTPS and an API budget limit is outside what it's designed for. See [docs/self-hosting.md](../docs/self-hosting.md#exposing-it-beyond-your-computer).

Within that scope, we care about anything that could leak an API key, let someone else spend your OpenAI credit or GPU time, read or write files outside the log directory, or run code from untrusted input (a viewer's line, a story file, a request to the server).

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this repository. Don't open a public issue for security problems. The project is maintained on a best-effort basis, so there's no guaranteed response time.

## If you exposed a key

If you committed or shared a key, revoke it and create a new one: OpenAI at <https://platform.openai.com/api-keys>, Hugging Face at <https://huggingface.co/settings/tokens>, Vast.ai at <https://cloud.vast.ai/manage-keys/>, Langfuse in the project's settings. Deleting the file or rewriting git history isn't enough. If you rented a GPU, check that no instance is left running (`server/vast_run.sh status`).
