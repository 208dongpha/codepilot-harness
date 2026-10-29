from rich.console import Console

console = Console(markup=False, highlight=False)


def show(text: str) -> None:
    console.print(text)
