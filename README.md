# Project MAGI

An AI decision-making system inspired by the MAGI System from *Neon Genesis Evangelion*.

Project MAGI combines multiple state-of-the-art large language models into a collaborative decision-making system. Instead of relying on a single AI, multiple agents analyze the same problem, debate with one another, and produce a final consensus.

---

# AI Members

### 🧠 Melchior
- Model: OpenAI GPT-5.5
- Role: Logic & Analytical Reasoning

### 📊 Balthasar
- Model: Google Gemini
- Role: Market Data & Trend Analysis

### ⚖️ Casper
- Model: Anthropic Claude
- Role: Risk Assessment & Conservative Judgment

---

# Current Architecture

```
                User Question
                      │
                      ▼
      ┌─────────────────────────────────┐
      │      Initial Responses          │
      └─────────────────────────────────┘
          │        │              │
          ▼        ▼              ▼
     Melchior   Balthasar      Casper
          │        │              │
          └────────┴──────────────┘
                     │
                     ▼
             Debate Engine
          (Multiple Discussion Rounds)
                     │
                     ▼
            Consensus Engine
                     │
                     ▼
              Final Decision
```

---

# Current Features

- ✅ OpenAI API integration
- ✅ Google Gemini API integration
- ✅ Anthropic Claude API integration
- ✅ Three independent AI agents
- ✅ Multi-round debate engine
- ✅ Consensus generation
- ✅ Modular architecture
- ✅ Secure API key management (.env)

---

# Example Workflow

```
Question

↓

Melchior Response

↓

Balthasar Response

↓

Casper Response

↓

Debate Round 1

↓

Debate Round 2

↓

Debate Round 3

↓

Final Consensus
```

---

# Example Question

```
Should I buy more SK Hynix at the current valuation for a 5-year investment?
```

The system generates:

- Initial independent opinions
- Multi-round debate
- Revised arguments
- Final consensus

---

# Repository Structure

```
Project MAGI

magi/
    melchior.py
    balthasar.py
    casper.py
    debate.py
    consensus.py

tests/
    test_openai.py
    test_gemini.py
    test_claude.py

main.py
README.md
requirements.txt
```

---

# Roadmap

## Version 1.3

- Persona specialization
- Better prompt engineering
- Improved debate quality

## Version 1.4

- Memory system
- Persistent conversation history
- User preference learning

## Version 2.0

- Real-time stock prices
- Financial statement analysis
- News integration
- Economic indicators

## Version 3.0

- Portfolio management
- Buy / Hold / Sell recommendations
- Confidence scoring
- Risk scoring

## Future Vision

- Web application
- Desktop application
- Voice interaction
- Multi-user support
- Cloud deployment
- Autonomous investment assistant

---

# Technology Stack

- Python
- OpenAI API
- Google Gemini API
- Anthropic Claude API
- Git
- GitHub
- VS Code

---

# Status

Current Version:

**v1.2**

Completed:

- OpenAI Integration
- Gemini Integration
- Claude Integration
- Debate Engine
- Consensus Engine

Project Status:

**Actively under development**