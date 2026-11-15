"""The assistant's own browser (phase 1: read-only with a live view).

A headless Chrome driven over the DevTools protocol on a pipe, with all traffic going
through a local filtering proxy that refuses anything that isn't a public address.
Nothing here reads the owner's own browser profile. Page content is untrusted data.
"""
