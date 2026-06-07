import os
import sys
from contextlib import asynccontextmanager
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(__file__))
from game import load_words, build_graph, largest_component, pick_puzzle

_words: set[str] = set()
_graph: dict = {}
_puzzle: dict | None = None
_puzzle_day: int = -1


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _words, _graph
    raw = load_words(5)
    graph = build_graph(raw)
    _words = largest_component(graph)
    _graph = graph
    yield


app = FastAPI(lifespan=lifespan)

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "https://diddle.retard.zone")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def today_puzzle() -> dict:
    global _puzzle, _puzzle_day
    today = date.today().toordinal()
    if _puzzle_day != today:
        start, end, path = pick_puzzle(_words, _graph)
        _puzzle = {
            "start": start,
            "end": end,
            "optimal_steps": len(path) - 1,
            "day": today % 10_000,
            "word_length": 5,
        }
        _puzzle_day = today
    return _puzzle


@app.get("/health")
async def health():
    return {"status": "ok", "day": date.today().toordinal() % 10_000}


@app.get("/puzzle")
async def puzzle():
    return today_puzzle()


@app.get("/words")
async def words():
    return PlainTextResponse("\n".join(sorted(_words)))


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
