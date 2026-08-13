"""AI Answer Tracking (Part A).

Executes tracked prompts against configured AI providers and stores the raw
responses. Performs NO analysis — mention detection, competitor extraction,
sentiment, and Share of Voice are Part B. The only mention logic here is the
deliberately narrow, temporary `_provisional_mention_check` used to decide the
adaptive third run (see runner.py).
"""
