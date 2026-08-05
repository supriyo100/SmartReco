"""LangGraph assembly — arch v2 §3. Build Aug 7.
State TypedDict: user_id, events, interests, interests_short, profile, queries,
candidates, ranked, grade, draft, retries, fallback_used.
Checkpoint with SqliteSaver. Trace to LangSmith when LANGSMITH_TRACING=true.
"""
