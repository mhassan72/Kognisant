import unittest

from cli_kognisant.agents import CPU_COUNT, MAX_LOCAL_CONCURRENCY, get_best_models_pool


class TestAgentsConcurrency(unittest.TestCase):
    def test_get_best_models_pool_empty_fallback(self):
        """Verify model selectors return safe offline fallbacks if the pool is empty."""
        planning, task = get_best_models_pool([])
        self.assertEqual(planning["name"], "mock")
        self.assertEqual(task["name"], "mock")

    def test_get_best_models_pool_routing(self):
        """Verify model selectors prioritize cloud models for planning and local models for tasks."""
        models = [
            {
                "name": "gemma3:1b",
                "provider": "Ollama (Local)",
                "api_base_url": "http://localhost:11434/v1",
                "api_key": "",
            },
            {
                "name": "gpt-4o-mini",
                "provider": "OpenAI",
                "api_base_url": "https://api.openai.com/v1",
                "api_key": "sk-proj-valid",
            },
        ]
        planning, task = get_best_models_pool(models)
        # OpenAI (cloud) model is best for planning
        self.assertEqual(planning["name"], "gpt-4o-mini")
        # Ollama model is preferred fallback for minor tasks
        self.assertEqual(task["name"], "gemma3:1b")

    def test_concurrency_boundaries(self):
        """Verify concurrency calculations remain valid and mathematically bounded."""
        self.assertGreaterEqual(CPU_COUNT, 1)
        self.assertGreaterEqual(MAX_LOCAL_CONCURRENCY, 1)
        self.assertEqual(MAX_LOCAL_CONCURRENCY, max(1, CPU_COUNT // 4))


if __name__ == "__main__":
    unittest.main()
