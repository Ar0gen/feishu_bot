FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_SYSTEM_PYTHON=1
ENV UV_NO_DEV=1

ADD pyproject.toml /app
ADD uv.lock /app
RUN uv sync --locked --no-install-project

ADD . /app
RUN uv sync --locked

EXPOSE 3000

ENTRYPOINT [ "uv", "run", "server.py" ]


