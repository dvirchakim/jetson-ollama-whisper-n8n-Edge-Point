#!/usr/bin/env python3
"""Gradio-based chat UI for Ollama with PostgreSQL-backed memory."""

import os
import json
import uuid
import logging
from typing import List, Tuple, Optional

import requests
import psycopg
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "0")
os.environ.setdefault("GRADIO_ALLOWED_PATHS", "/app")

import gradio as gr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ollama-ui")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "PetrosStav/gemma3-tools:4b")
SYSTEM_PROMPT = os.getenv(
    "OLLAMA_SYSTEM_PROMPT",
    "You are a helpful AI assistant running on a Jetson edge device."
)
MAX_HISTORY_MESSAGES = int(os.getenv("OLLAMA_UI_MAX_HISTORY", "40"))

DB_CONN_INFO = {
    "host": os.getenv("POSTGRES_HOST", "postgres"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "ollama_memory"),
    "user": os.getenv("POSTGRES_USER", "ollama"),
    "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
}


def get_conn():
    return psycopg.connect(**DB_CONN_INFO, autocommit=True)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS conversation_history_session_created_idx
                ON conversation_history (session_id, created_at);
                """
            )
            cur.execute(
                "ALTER TABLE conversation_history ADD COLUMN IF NOT EXISTS metadata TEXT;"
            )
    logger.info("Database initialized")


def append_message(session_id: str, role: str, content: str, metadata: dict) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversation_history (session_id, role, content, metadata) VALUES (%s, %s, %s, %s)",
                (session_id, role, content, json.dumps(metadata, indent=2)),
            )


def fetch_history(session_id: str) -> List[Tuple[str, str, dict]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content, metadata
                FROM conversation_history
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (session_id, MAX_HISTORY_MESSAGES),
            )
            return cur.fetchall()


def get_all_sessions() -> List[Tuple[str, str, str]]:
    """Get all unique sessions with their first message and timestamp."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (session_id)
                    session_id,
                    content,
                    created_at::text
                FROM conversation_history
                WHERE role = 'user'
                ORDER BY session_id, created_at ASC
                """
            )
            return cur.fetchall()


def delete_session(session_id: str) -> None:
    """Delete all messages from a specific session."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversation_history WHERE session_id = %s",
                (session_id,)
            )
    logger.info(f"Deleted session: {session_id}")


def delete_all_sessions() -> None:
    """Delete all conversation history."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversation_history")
    logger.info("Deleted all sessions")


def history_to_chat_pairs(rows: List[Tuple[str, str, dict]]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    current_user: Optional[str] = None
    for role, content, _ in rows:
        if role == "user":
            current_user = content
        elif role == "assistant":
            pairs.append((current_user or "", content))
            current_user = None
    if current_user:
        pairs.append((current_user, ""))
    return pairs


def build_messages(rows: List[Tuple[str, str, dict]], user_message: str):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content, _ in rows:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def chat_with_ollama(messages):
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    reply = data.get("message", {}).get("content")
    metadata = data.get("metadata", {})
    if not reply:
        raise RuntimeError("Empty response from Ollama")
    return reply, metadata


def handle_chat(user_message: str, history: List[Tuple[str, str]], session_id: str):
    if not user_message:
        return history, gr.update(), session_id

    stored_history = fetch_history(session_id)
    messages = build_messages(stored_history, user_message)

    append_message(session_id, "user", user_message, {})

    try:
        assistant_reply, metadata = chat_with_ollama(messages)
    except Exception as exc:
        logger.exception("Chat failed")
        assistant_reply = f"Error: {exc}"
        metadata = {}

    append_message(session_id, "assistant", assistant_reply, metadata)

    history = history + [(user_message, assistant_reply)]
    return history, json.dumps(metadata, indent=2), session_id


def load_history(session_id: str):
    rows = fetch_history(session_id)
    return history_to_chat_pairs(rows)


def new_session():
    new_id = str(uuid.uuid4())
    return new_id, []


def build_interface():
    init_db()

    with gr.Blocks(title="Jetson Ollama Chat") as demo:
        gr.Markdown("""
        # Jetson Ollama Chat UI
        Chat with your on-device Ollama model. Conversations persist in PostgreSQL so you can resume anytime.
        """)

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### Conversation History")
                session_list = gr.Dropdown(
                    choices=[],
                    label="Previous Sessions",
                    interactive=True
                )
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh", size="sm")
                    new_session_btn = gr.Button("➕ New", size="sm", variant="primary")
                with gr.Row():
                    delete_current_btn = gr.Button("🗑️ Delete Current", size="sm", variant="stop")
                    delete_all_btn = gr.Button("⚠️ Delete All", size="sm", variant="stop")
                
            with gr.Column(scale=3):
                session_state = gr.State(str(uuid.uuid4()))
                chatbot = gr.Chatbot(label="Conversation", height=400)
                user_input = gr.Textbox(label="Your message", placeholder="Say something...", autofocus=True)
                metadata_output = gr.Textbox(label="Metadata", lines=4)

        def load_session(sess_id):
            return load_history(sess_id)
        
        def refresh_sessions():
            sessions = get_all_sessions()
            choices = [(f"{content[:50]}... ({created_at[:19]})", session_id) 
                      for session_id, content, created_at in sessions]
            return gr.update(choices=choices)
        
        def switch_session(selected_session_id, current_session_id):
            if not selected_session_id:
                return load_history(current_session_id), current_session_id
            history = load_history(selected_session_id)
            return history, selected_session_id

        demo.load(load_session, inputs=[session_state], outputs=[chatbot])
        demo.load(refresh_sessions, inputs=None, outputs=[session_list])

        user_input.submit(
            handle_chat,
            inputs=[user_input, chatbot, session_state],
            outputs=[chatbot, metadata_output, session_state],
        )

        def reset_session():
            new_id, empty_history = new_session()
            return empty_history, "", new_id, refresh_sessions()

        new_session_btn.click(
            reset_session,
            inputs=None,
            outputs=[chatbot, user_input, session_state, session_list],
        )
        
        refresh_btn.click(
            refresh_sessions,
            inputs=None,
            outputs=[session_list],
        )
        
        session_list.change(
            switch_session,
            inputs=[session_list, session_state],
            outputs=[chatbot, session_state],
        )
        
        def delete_current_session(current_session_id):
            delete_session(current_session_id)
            new_id, empty_history = new_session()
            return empty_history, "", new_id, refresh_sessions()
        
        delete_current_btn.click(
            delete_current_session,
            inputs=[session_state],
            outputs=[chatbot, user_input, session_state, session_list],
        )
        
        def erase_all_conversations():
            delete_all_sessions()
            new_id, empty_history = new_session()
            return empty_history, "", new_id, refresh_sessions()
        
        delete_all_btn.click(
            erase_all_conversations,
            inputs=None,
            outputs=[chatbot, user_input, session_state, session_list],
        )

    return demo


def main():
    demo = build_interface()
    port = int(os.getenv("PORT", os.getenv("OLLAMA_UI_PORT", "7861")))
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        inbrowser=False,
        share=False,
    )


if __name__ == "__main__":
    main()
