try:
    import agno
    import agno.agent
    import agno.models.openai

    print("agno.agent:", dir(agno.agent))
    print("agno.models.openai:", dir(agno.models.openai))

    try:
        import agno.db.sqlite

        print("agno.db.sqlite:", dir(agno.db.sqlite))
    except ImportError:
        print("agno.db.sqlite not found")

    try:
        import agno.storage

        print("agno.storage:", dir(agno.storage))
    except ImportError:
        print("agno.storage not found")

except ImportError as e:
    print(e)
except Exception as e:
    print(e)
