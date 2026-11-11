class ProblemAssertions:
    """For tests of functions that return a list of human-readable problems."""

    def assertProblem(self, problems, *words):
        matching = [p for p in problems if all(w in p for w in words)]
        self.assertTrue(matching, f"no problem mentioning {words} in {problems}")
