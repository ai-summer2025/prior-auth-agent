FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY data ./data
# No network or API key needed for the default (rules) mode.
ENTRYPOINT ["priorauth"]
CMD ["check", "data/requests/lumbar_mri_red_flag.json"]
