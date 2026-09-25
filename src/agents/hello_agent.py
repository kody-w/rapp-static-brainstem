from basic_agent import BasicAgent


class HelloAgent(BasicAgent):
    def __init__(self):
        self.name = "Hello"
        self.metadata = {
            "name": self.name,
            "description": "Greet someone by name. A tiny agent that proves the static brainstem can run tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Who to greet."}
                },
                "required": ["name"]
            }
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        name = str(kwargs.get("name", "")).strip() or "friend"
        return f"Hello, {name}. Your global brainstem is answering from static files."
