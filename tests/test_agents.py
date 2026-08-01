import unittest

from cli_kognisant.agents import CPU_COUNT, MAX_LOCAL_CONCURRENCY, get_best_models_pool


class TestAgentsConcurrency(unittest.TestCase):
    def test_get_best_models_pool_empty_fallback(self):
        """Verify model selectors return safe offline fallbacks if the pool is empty."""
        planning, task = get_best_models_pool([])
        self.assertEqual(planning["name"], "mock")
        self.assertEqual(task["name"], "mock")

    def test_get_best_models_pool_routing(self):
        """Verify model selectors prioritize cloud models for planning and cheapest for tasks."""
        models = [
            {
                "name": "gemma3:1b",
                "provider": "Ollama (Local)",
                "api_base_url": "http://localhost:11434/v1",
                "api_key": "",
                "capabilities": {"tool_calling": True, "reasoning": False, "context_window": 131072},
            },
            {
                "name": "gpt-4o-mini",
                "provider": "OpenAI",
                "api_base_url": "https://api.openai.com/v1",
                "api_key": "sk-proj-valid",
                "capabilities": {"tool_calling": True, "reasoning": True, "context_window": 128000},
            },
            {
                "name": "MiniMaxAI/MiniMax-M3",
                "provider": "Kognisant Cloud",
                "api_base_url": "https://inference.kognisant.xyz/v1/",
                "api_key": "",
                "capabilities": {"tool_calling": True, "reasoning": True, "context_window": 1049000},
                "_kognisant_hosted": True,
                "_pricing": {"input_per_million": 0.30, "output_per_million": 1.20},
            },
            {
                "name": "nvidia/Cosmos3-Super-Reasoner",
                "provider": "Kognisant Cloud",
                "api_base_url": "https://inference.kognisant.xyz/v1/",
                "api_key": "",
                "capabilities": {"tool_calling": True, "reasoning": True, "context_window": 256000},
                "_kognisant_hosted": True,
                "_pricing": {"input_per_million": 0.10, "output_per_million": 0.30},
            },
        ]
        planning, task = get_best_models_pool(models)
        # Kognisant Cloud model with largest context wins for planning
        self.assertEqual(planning["name"], "MiniMaxAI/MiniMax-M3")
        # Cheapest cloud model with tool_calling wins for task workers
        self.assertEqual(task["name"], "nvidia/Cosmos3-Super-Reasoner")

    def test_concurrency_boundaries(self):
        """Verify concurrency calculations remain valid and mathematically bounded."""
        self.assertGreaterEqual(CPU_COUNT, 1)
        self.assertGreaterEqual(MAX_LOCAL_CONCURRENCY, 1)
        self.assertEqual(MAX_LOCAL_CONCURRENCY, max(1, CPU_COUNT // 4))


if __name__ == "__main__":
    unittest.main()
