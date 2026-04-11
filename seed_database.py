name: Seed Database
on:
  workflow_dispatch:
jobs:
  seed:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install anthropic
      - run: python seed_database.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: |
          git config user.name "morning-briefing-bot"
          git config user.email "bot@users.noreply.github.com"
          git add docs/data/companies.json
          git diff --staged --quiet || git commit -m "seed: initial company database"
          git push
