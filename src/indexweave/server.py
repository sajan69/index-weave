"""Launch one local workspace; network binding is deliberately fixed."""

import argparse


def main():
    parser = argparse.ArgumentParser(
        description="Serve an existing IndexWeave workspace locally."
    )
    parser.add_argument("workspace")
    parser.add_argument(
        "--port",
        type=int,
        choices=range(1024, 65536),
        default=8765,
        metavar="1024..65535",
    )
    parser.add_argument(
        "--model", help="Verified local model directory; never downloaded by the server"
    )
    args = parser.parse_args()
    import uvicorn

    from .api import create_app

    app = create_app(args.workspace, port=args.port, model_path=args.model)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        limit_concurrency=16,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
