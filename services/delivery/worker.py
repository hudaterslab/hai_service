from delivery.app.bootstrap import build_delivery_worker


def main() -> None:
    build_delivery_worker().run_forever()


if __name__ == "__main__":
    main()
