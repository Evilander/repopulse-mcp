FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir -e .

# Mount the repo you want to index/serve at /repo and speak MCP over stdio.
# Example:
#   docker run --rm -i -v /path/to/repo:/repo repopulse-mcp
WORKDIR /repo
ENTRYPOINT ["repopulse"]
CMD ["serve", "--repo", "/repo"]
