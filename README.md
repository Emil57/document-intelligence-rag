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

### Or chat in the terminal

Put some files in a `samples/` folder in the project, then:

```bash
uv run src/document_intelligence_rag/experiments/chat.py
```

Type questions; `add <file or link>` adds a source, `new` starts a fresh conversation,
`quit` exits. Each tool the agent uses is printed, so you can watch it work.

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
├── chunking.py      split documents into overlapping chunks
├── search.py        BM25 keyword search
├── rag.py           one-shot RAG: search once, then ask Claude (step 4)
├── agent.py         the agent: tools, agent loop and conversation memory
├── auth.py          password hashing and login tokens
├── store.py         SQLite database: users, sessions, libraries, members, documents
├── web.py           FastAPI server and API, with permission checks
├── static/
│   └── index.html   the web page
└── experiments/     small scripts from each learning step
    ├── first_call.py      step 1: first Claude call
    ├── ask_a_file.py      step 2: ask about one file
    ├── search_files.py    step 3: search without AI
    ├── ask_library.py     step 4: RAG answer with sources
    └── chat.py            steps 5–6: chat with the agent in the terminal
```

Each experiment explains how to run it at the top of the file.

## Configuration

Optional settings go in `.env`:

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Claude API key |
| `DOCINTEL_MODEL` | `claude-haiku-5-5` | Model that answers questions and describes images. Try `claude-sonnet-5-5` or `claude-opus-5-5` for better answers |
| `DOCINTEL_SIGNUP_CODE` | (none) | If set, creating an account requires this code |
| `DOCINTEL_DATA_DIR` | `data` | Where the web app saves its database (`app.db`) and uploaded files (`files/<library id>/`) |

The `data/` folder is in `.gitignore` because it holds accounts and private documents.
Passwords are stored only as salted scrypt hashes, and login tokens only as SHA-256 hashes.

## Costs

The API is billed per token (roughly ¾ of a word). With the default model, Claude Haiku 5.5
($0.10 per million input tokens, $0.50 per million output), a typical question costs well under
a tenth of a cent. The web page and terminal chat show the tokens used for every answer.
Set a monthly spend limit at platform.claude.com to avoid surprises.

## Roadmap

- [x] 1. First Claude call
- [x] 2. Loaders for PDF, Word and text files
- [x] 3. Chunking and keyword search
- [x] 4. RAG answers with cited sources
- [x] 5. Agent with tools and conversation memory
- [x] 6. Web links and image descriptions
- [x] 7. Web app with a saved library
- [x] 8. Users and sharing: sign-in, private libraries, shared libraries for groups
- [ ] 9. Going online: hosting with HTTPS, secure cookies, login rate limits, and protecting
      `add_link` from reaching private network addresses
- [ ] Later: search by meaning (embeddings), audio/video transcription, answer-quality tests

## Known limitations

- No HTTPS yet, and no limit on login attempts (step 9)
- Libraries from the step 7 version (`data/library.json`) aren't imported; upload them again
- Conversations live in memory, so restarting the server starts fresh chats (the library stays)
- Scanned PDFs (photos of pages) have no text to extract
- Old Word `.doc` files aren't supported, only `.docx`
- Audio and video aren't supported yet
- Some websites block automated downloads
