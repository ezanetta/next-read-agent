"""The agent loop.

Retrieves candidates, lets the LLM rerank them and call tools, and validates the hard
rules (three books, none already read, filters respected, valid links) before accepting
a recommendation. Not implemented yet.
"""
