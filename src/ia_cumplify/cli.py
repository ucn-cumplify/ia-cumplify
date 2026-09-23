import uvicorn


def main() -> None:
    uvicorn.run(
        "ia_cumplify.adapters.inbound.http.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
