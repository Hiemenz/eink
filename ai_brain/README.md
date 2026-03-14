# AI Brain — Autonomous Agent Framework for Raspberry Pi

An evolving, continuously-running AI assistant that plans tasks, spawns sub-agents,
executes actions, learns from memory, and improves its capabilities over time.

---

## Architecture

```
ai_brain/
├── brain.py            ← Central reasoning loop
├── orchestrator.py     ← Task queue & agent dispatch
├── discord_bridge.py   ← Discord feedback + objectives
├── main.py             ← Daemon entry point
│
├── agents/
│   ├── base_agent.py   ← Base Agent class
│   ├── research_agent.py   ← Web search & summarisation
│   ├── builder_agent.py    ← Code writing & execution
│   ├── operator_agent.py   ← Shell commands & file ops
│   └── planner_agent.py    ← Goal decomposition
│
├── tools/
│   ├── web_search.py   ← DuckDuckGo (no API key needed)
│   ├── file_manager.py ← Read/write/list/delete
│   ├── code_runner.py  ← Safe subprocess execution
│   ├── git_tools.py    ← Git status/diff/commit
│   └── scheduler.py    ← Job queue helpers
│
├── skills/             ← Auto-discovered plugins
│   ├── crypto_monitor.py
│   ├── weather_agent.py
│   └── system_health.py
│
├── memory/
│   └── store.py        ← DuckDB persistent memory
│
├── scheduler/
│   └── job_scheduler.py ← Background periodic jobs
│
├── llm/
│   └── interface.py    ← Anthropic / OpenAI / Ollama
│
└── config/
    ├── config.yaml
    └── loader.py
```

---

## How It Works

The Brain runs in a continuous loop:

```
while True:
    1. Run pending agent tasks (orchestrator)
    2. Read memory (objectives, events, knowledge)
    3. Ask LLM: "what should I do next?"
    4. Act: spawn agent | run skill | plan goal
    5. Post status to Discord (every 5 cycles)
    6. Sleep(reflection_interval)
```

---

## Quick Start

### 1. Install

```bash
git clone <repo>
cd eink
poetry install
# For Discord support:
poetry install --extras discord
```

### 2. Configure

Copy and edit the config:
```bash
cp ai_brain/config/config.yaml config.local.yaml
```

Set environment variables:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Optional:
export DISCORD_TOKEN=your-bot-token
export DISCORD_CHANNEL_ID=123456789
```

Or use a `.env` file and `python-dotenv`.

### 3. Run

```bash
# Start brain daemon
poetry run python -m ai_brain.main

# With initial objective
poetry run python -m ai_brain.main --objective "Research the latest in edge AI"

# With custom config
poetry run python -m ai_brain.main --config config.local.yaml
```

---

## Raspberry Pi Setup

### Prerequisites

```bash
# Install Python 3.11+ (Raspberry Pi OS Bookworm ships with 3.11)
sudo apt update && sudo apt install -y python3-pip git

# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Install project
git clone <repo> ~/ai-brain
cd ~/ai-brain
poetry install
```

### Run as a systemd service (auto-restart)

```ini
# /etc/systemd/system/ai-brain.service
[Unit]
Description=AI Brain Daemon
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/eink
EnvironmentFile=/home/pi/eink/.env
ExecStart=/home/pi/.local/bin/poetry run python -m ai_brain.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-brain
sudo systemctl start ai-brain
sudo journalctl -u ai-brain -f
```

### LLM options for Pi (no GPU required)

| Provider | Best for Pi | Notes |
|----------|-------------|-------|
| Anthropic Claude Haiku | ✅ Recommended | Fast, cheap, cloud API |
| OpenAI GPT-4o-mini | ✅ Good | Cloud API |
| Ollama + Mistral 7B | ✅ Fully local | Needs Pi 5 (8GB RAM) |

For Ollama:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral
# Then in config.yaml:
# llm:
#   provider: ollama
#   model: mistral
```

---

## Discord Integration

The Discord bridge lets you **set objectives** and **monitor the brain** from your phone.

### Setup

1. Go to https://discord.com/developers/applications
2. Create a new application → Bot → enable **Message Content Intent**
3. Invite the bot to your server (OAuth2 → `bot` scope, `Send Messages` + `Read Messages`)
4. Set env vars:
   ```bash
   export DISCORD_TOKEN=your-bot-token
   export DISCORD_CHANNEL_ID=123456789012345678
   ```

### Commands

| Command | Description |
|---------|-------------|
| `!help` | Show all commands |
| `!status` | Brain status report |
| `!objectives` | List active objectives |
| `!add <text>` | Add a new objective |
| `!done <id>` | Mark objective complete |
| `!tasks` | Show task queue |
| `!thoughts` | Recent brain reasoning |
| _(any text)_ | Automatically becomes an objective |

---

## Memory System (DuckDB)

All data is stored in `brain.db` — a single portable file.

| Table | Contents |
|-------|----------|
| `events` | Every agent action with timestamp |
| `tasks` | Task queue with status |
| `knowledge` | Learned facts and summaries |
| `thoughts` | Brain reasoning journal |
| `objectives` | User-set goals (from Discord or CLI) |

Query the memory directly:
```bash
poetry run python -c "
import duckdb
conn = duckdb.connect('brain.db')
print(conn.execute('SELECT * FROM thoughts ORDER BY timestamp DESC LIMIT 5').fetchdf())
"
```

---

## Adding Skills

Create a file in `ai_brain/skills/` with this structure:

```python
# ai_brain/skills/my_skill.py

SKILL_NAME = "my_skill"
SKILL_DESCRIPTION = "What this skill does"
SCHEDULE_INTERVAL = 3600  # seconds (0 = manual only)

def run(memory, llm) -> str:
    result = do_something()
    memory.save_knowledge("my_topic", result)
    return result
```

The brain auto-discovers and loads it on next startup. Scheduled skills
run automatically via the job scheduler.

---

## Configuration Reference

```yaml
brain:
  reflection_interval: 60      # seconds between brain cycles
  max_thoughts_per_cycle: 3    # max LLM calls per cycle
  verbose: true

agents:
  max_parallel: 1              # increase for Pi 5 / multi-core
  timeout: 300
  retry_attempts: 2

memory:
  database: brain.db

llm:
  provider: anthropic          # anthropic | openai | ollama
  model: claude-haiku-4-5-20251001
  temperature: 0.7
  max_tokens: 2048

scheduler:
  check_interval: 10
```

---

## Example Brain Behaviour

```
Cycle #1
  Brain reads: objective "Research edge AI trends"
  Brain decides: spawn ResearchAgent
  ResearchAgent searches DuckDuckGo, summarises, stores in knowledge table

Cycle #2
  Brain reads: knowledge about edge AI
  Brain decides: spawn BuilderAgent to write a summary script
  BuilderAgent generates Python script, writes to disk

Cycle #3
  Brain reads: script was written
  Brain decides: spawn OperatorAgent to run it
  OperatorAgent executes, logs output

Cycle #5
  Brain posts status to Discord
  User replies: "!add Monitor crypto prices daily"
  Discord bridge stores as objective
  Next cycle brain spawns crypto_monitor skill
```
