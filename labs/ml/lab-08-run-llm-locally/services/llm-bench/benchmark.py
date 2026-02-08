"""
Run standardized benchmarks against the LLM Benchmark Service.

Usage:
    python benchmark.py                  # benchmark tinyllama (default)
    python benchmark.py phi              # benchmark phi model
    python benchmark.py tinyllama 5      # 5 runs per prompt
"""
import requests
import sys
import time
import json

API_URL = "http://localhost:8000"

PROMPTS = [
    {"name": "short", "prompt": "What is 2+2?", "max_tokens": 20},
    {
        "name": "medium",
        "prompt": "Explain what a neural network is in 3 sentences.",
        "max_tokens": 100,
    },
    {
        "name": "long",
        "prompt": "Write a Python function that implements binary search. Include docstring and comments.",
        "max_tokens": 200,
    },
    {
        "name": "creative",
        "prompt": "Write a haiku about distributed systems.",
        "max_tokens": 50,
    },
]


def check_service():
    """Verify the benchmark service and Ollama are reachable."""
    try:
        resp = requests.get(f"{API_URL}/health", timeout=5)
        data = resp.json()
        if not data.get("ollama_reachable"):
            print("WARNING: Ollama is not reachable from the bench service.")
            return False
        return True
    except requests.ConnectionError:
        print(f"ERROR: Cannot reach benchmark service at {API_URL}")
        print("Make sure to run: docker compose up -d")
        return False


def pull_model(model):
    """Pull a model if not already available."""
    print(f"Ensuring model '{model}' is available...")
    resp = requests.post(f"{API_URL}/pull", json={"model": model}, timeout=600)
    data = resp.json()
    print(f"  Status: {data['status']}")
    return data


def list_models():
    """List locally available models."""
    resp = requests.get(f"{API_URL}/models", timeout=10)
    data = resp.json()
    models = data.get("models", [])
    if models:
        print("\nAvailable models:")
        for m in models:
            size_gb = m.get("size", 0) / (1024**3)
            print(f"  - {m['name']} ({size_gb:.1f} GB)")
    else:
        print("\nNo models available. Pull one first.")
    return models


def run_benchmark(model="tinyllama", runs=3):
    """Run the benchmark suite."""
    print(f"\n{'=' * 70}")
    print(f"  LLM Benchmark: {model} ({runs} runs per prompt)")
    print(f"{'=' * 70}")

    header = f"  {'Prompt':12s} | {'TTFT':>10s} | {'TPS':>8s} | {'Total':>10s} | {'Tokens':>6s}"
    divider = f"  {'-' * 12}-+-{'-' * 10}-+-{'-' * 8}-+-{'-' * 10}-+-{'-' * 6}"

    print(f"\n{header}")
    print(divider)

    all_results = []

    for prompt_config in PROMPTS:
        ttfts, tpss, totals, token_counts = [], [], [], []

        for i in range(runs):
            try:
                resp = requests.post(
                    f"{API_URL}/generate",
                    json={
                        "prompt": prompt_config["prompt"],
                        "model": model,
                        "max_tokens": prompt_config["max_tokens"],
                    },
                    timeout=120,
                )
                data = resp.json()
                ttfts.append(data["ttft_ms"])
                tpss.append(data["tokens_per_second"])
                totals.append(data["total_time_ms"])
                token_counts.append(data["token_count"])
            except Exception as e:
                print(f"  ERROR on run {i + 1} of '{prompt_config['name']}': {e}")

        if ttfts:
            avg_ttft = sum(ttfts) / len(ttfts)
            avg_tps = sum(tpss) / len(tpss)
            avg_total = sum(totals) / len(totals)
            avg_tokens = sum(token_counts) / len(token_counts)

            print(
                f"  {prompt_config['name']:12s} | "
                f"{avg_ttft:8.0f}ms | "
                f"{avg_tps:6.1f}/s | "
                f"{avg_total:8.0f}ms | "
                f"{avg_tokens:5.0f}"
            )

            all_results.append(
                {
                    "name": prompt_config["name"],
                    "avg_ttft_ms": avg_ttft,
                    "avg_tps": avg_tps,
                    "avg_total_ms": avg_total,
                    "avg_tokens": avg_tokens,
                }
            )

    print(f"\n{'=' * 70}")

    if all_results:
        overall_ttft = sum(r["avg_ttft_ms"] for r in all_results) / len(all_results)
        overall_tps = sum(r["avg_tps"] for r in all_results) / len(all_results)
        print(f"  Overall avg TTFT:  {overall_ttft:.0f}ms")
        print(f"  Overall avg TPS:   {overall_tps:.1f} tokens/sec")
        print(f"{'=' * 70}\n")

    return all_results


def stream_demo(model="tinyllama"):
    """Show streaming generation in real time."""
    prompt = "Write a limerick about a programmer debugging code."
    print(f"\n--- Streaming Demo ({model}) ---")
    print(f"Prompt: {prompt}\n")
    print("Response: ", end="", flush=True)

    resp = requests.post(
        f"{API_URL}/generate/stream",
        json={"prompt": prompt, "model": model, "max_tokens": 100},
        stream=True,
        timeout=120,
    )

    for line in resp.iter_lines():
        if line:
            text = line.decode("utf-8")
            if text.startswith("data: "):
                data = json.loads(text[6:])
                if "token" in data:
                    print(data["token"], end="", flush=True)
                elif data.get("done"):
                    print(f"\n\n--- Stats ---")
                    print(f"  TTFT:       {data['ttft_ms']:.0f}ms")
                    print(f"  TPS:        {data['tokens_per_second']:.1f}")
                    print(f"  Total:      {data['total_time_ms']:.0f}ms")
                    print(f"  Tokens:     {data['token_count']}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "tinyllama"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    if not check_service():
        sys.exit(1)

    pull_model(model)
    list_models()
    run_benchmark(model, runs)
    stream_demo(model)
