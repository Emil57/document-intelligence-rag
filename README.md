# document-intelligence-rag

An AI agent you can share documents, images and links with, then ask questions about them.
Answers come only from your library and cite the file they came from.

Built step by step as a learning project, using Python, [uv](https://docs.astral.sh/uv/)
and the [Claude API](https://platform.claude.com).

## Features

- **Many kinds of sources:** PDF, Word (`.docx`), text (`.txt`, `.md`, `.csv`, `.json`), HTML,
  images (`.png`, `.jpg`, `.gif`, `.webp`) and web links
- **Images become searchable:** Claude transcribes the text in each image and describes the rest
- **Answers with sources:** every fact is cited, e.g. `[meeting_notes.docx]`. If the library
  doesn't contain the answer, it says so instead of guessing
- **An agent, not just a search box:** Claude decides whether to search, list or read whole
  documents, and searches again with other words when the first try finds nothing
- **Conversations:** follow-up questions such as "and who decided that?" work
- **Web app:** upload files, add links and chat in your browser. Everything is saved to disk
- **Accounts and sharing:** everyone gets a private library, and can create shared libraries
  and invite others by username

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and an Anthropic API key
from [platform.claude.com](https://platform.claude.com) (pay-as-you-go: see [Costs](#costs)).

```bash
git clone https://github.com/Emil57/document-intelligence-rag.git
cd document-intelligence-rag
uv sync                      # installs Python 3.12 and all packages
```

Create a file named `.env` in the project folder with your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is in `.gitignore`, so the key is never committed. Never share it.

### Run the web app

```bash
uv run uvicorn document_intelligence_rag.web:app --reload
```

Open http://127.0.0.1:8000, create an account, upload some files or add a link, and start
asking questions. Press Ctrl+C to stop the server.

To work with others, choose **+ Shared**, name the library, and invite people by their
username (they need to sign up first). In a shared library, members can add documents and
remove their own; the owner can remove anything and manage members.

> **Before letting others reach the app** (for example with `--host 0.0.0.0`), set
> `DOCINTEL_SIGNUP_CODE` so strangers can't create accounts and spend your API credits.
> The app doesn't use HTTPS yet, so only do this on a network you trust.

### Run the tests

```bash
uv run pytest
```

The tests use a fake Claude client, so they're free and need no API key. They cover chunking,
search, the agent loop, and the web app's access rules (who can see and change what).
GitHub Actions runs them on every push and pull request.

### Deploy

Every push to `main` is tested, built into a Docker image and deployed to a server on AWS
Lightsail, with HTTPS from Caddy. The one-time server setup is in
[docs/deploy-aws.md](docs/deploy-aws.md). Until it's done, the workflow only runs the tests.

## How it works

```
 adding a source                           asking a question

 file / image / link                       question
        │                                      │
        ▼                                      ▼
 loaders.py   → plain text                 agent.py  ◄──────────────┐
        │       (images: described         Claude decides what      │ tool
        ▼        by Claude)                to do next               │ results
 chunking.py  → ~1,000-character pieces        │                    │
        │       that overlap a little          ├─ search_library ───┤
        ▼                                      ├─ list_documents ───┤
 search.py    → keyword index (BM25),          ├─ read_document ────┤
                one per library                ├─ add_link ─────────┘
 store.py     → saved in data/app.db
                                               ▼
                                         answer with [citations]
```

This pattern is called **RAG** (retrieval-augmented generation): look up the relevant text
first, then have the model answer from it. Only the matching pieces are sent to Claude, which
keeps answers grounded and cheap even for large libraries.

Search uses **BM25**, a classic keyword-ranking formula: rare words count more, and long chunks
don't win just for being long. It needs no extra services, but it matches words, not meaning
("car" won't find "vehicle"). The agent works around this by searching again with synonyms.

## Project layout

```
src/document_intelligence_rag/
├── config.py        settings: model, data folder, chunks per question
├── loaders.py       files, images and links → Document (plain text)
├── fetch.py         safe link downloads: public addresses only, size and time limits
├── chunking.py      split documents into overlapping chunks
├── search.py        BM25 keyword search
├── agent.py         the agent: tools, agent loop and conversation memory
├── auth.py          password hashing and login tokens
├── store.py         SQLite database: users, sessions, libraries, members, documents, usage
├── spending.py      what Claude calls cost, and the daily limits
├── web.py           FastAPI server and API, with permission checks
└── static/
    └── index.html   the web page

Dockerfile           the app's Docker image
deploy/              the server setup: Docker Compose and Caddy (HTTPS)
.github/workflows/   tests on every push; deploy on pushes to main
docs/deploy-aws.md   one-time server setup on AWS Lightsail
```

## Configuration

Optional settings go in `.env`:

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Claude API key |
| `DOCINTEL_MODEL` | `claude-haiku-5-5` | Model that answers questions and describes images. Try `claude-sonnet-5-5` or `claude-opus-5-5` for better answers |
| `DOCINTEL_SIGNUP_CODE` | (none) | If set, creating an account requires this code |
| `DOCINTEL_USER_DAILY_LIMIT_USD` | `0.50` | Most each user can spend on Claude per day (UTC) |
| `DOCINTEL_APP_DAILY_LIMIT_USD` | `5.00` | Most all users together can spend per day |
| `DOCINTEL_DATA_DIR` | `data` | Where the web app saves its database (`app.db`) and uploaded files (`files/<library id>/`) |

The `data/` folder is in `.gitignore` because it holds accounts and private documents.
Passwords are stored only as salted scrypt hashes, and login tokens only as SHA-256 hashes.

## Costs

The API is billed per token (roughly ¾ of a word). With the default model, Claude Haiku 5.5
($0.10 per million input tokens, $0.50 per million output), a typical question costs well under
a tenth of a cent. The web page shows the tokens used for every answer.

Every Claude call is recorded with its estimated cost. The person asking pays for a question
(even in a shared library) and the uploader for an image description. When a user, or the app
as a whole, reaches its daily limit, questions and image uploads stop until midnight UTC; the page
shows each user's spending for the day. The costs are estimates from `spending.py`'s price
table, so also set a monthly spend limit at platform.claude.com as a final safety net.

## Roadmap

- [x] 1. First Claude call
- [x] 2. Loaders for PDF, Word and text files
- [x] 3. Chunking and keyword search
- [x] 4. RAG answers with cited sources
- [x] 5. Agent with tools and conversation memory
- [x] 6. Web links and image descriptions
- [x] 7. Web app with a saved library
- [x] 8. Users and sharing: sign-in, private libraries, shared libraries for groups
- [ ] 9. Going online
  - [x] Links can't reach private or internal network addresses
  - [x] Spending limits per user and for the whole app
  - [ ] Login rate limits
  - [x] Production settings: secure cookies on HTTPS, one worker, health check
  - [x] Deploy with Docker and GitHub Actions to AWS Lightsail, with HTTPS from Caddy
  - [ ] Backups: turn on Lightsail's automatic snapshots (see the deploy guide)
- [ ] Later: search by meaning (embeddings), audio/video transcription, answer-quality tests

## Known limitations

- No limit on login attempts yet (step 9)
- Libraries from the step 7 version (`data/library.json`) aren't imported; upload them again
- Conversations live in memory, so restarting the server starts fresh chats (the library stays)
- Scanned PDFs (photos of pages) have no text to extract
- Old Word `.doc` files aren't supported, only `.docx`
- Audio and video aren't supported yet
- Some websites block automated downloads
