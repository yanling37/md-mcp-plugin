def main():
    from .server import create_server
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
