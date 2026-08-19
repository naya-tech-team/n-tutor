"""The GraphQL layer: types, context, and the schema itself.

Named `graph/`, not `graphql/`, on purpose: a local package called `graphql`
sits on `sys.path` ahead of the installed **graphql-core** library when you run
`python app/main.py`, and Strawberry's own `from graphql import ...` then finds
your folder instead of the library.
"""
