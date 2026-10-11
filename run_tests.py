import json
import sys
import BEM_solver_agent
import check_answer as checker
from BEM_solver_agent import ask_agent
from check_answer import check_answer
from test_questions import QUESTIONS

# agent = Haiku 4.5 ($1 / $5 per million tokens); judge priced as $3 / $15 to stay on the safe side
DEFAULT_BUDGET = 0.10


def spent():
    agent = BEM_solver_agent.USAGE["in"] * 1e-6 + BEM_solver_agent.USAGE["out"] * 5e-6
    judge = checker.JUDGE_USAGE["in"] * 3e-6 + checker.JUDGE_USAGE["out"] * 15e-6
    return agent + judge


def main():
    args = sys.argv[1:]
    use_judge = "--no-judge" not in args
    judge_only = [int(a.split("=")[1]) for a in args if a.startswith("--judge=")]
    budget = next((float(a.split("=")[1]) for a in args if a.startswith("--budget=")), DEFAULT_BUDGET)
    only = [int(a) for a in args if a.isdigit()]

    results = []
    for number, item in enumerate(QUESTIONS, start=1):
        if only and number not in only:
            continue
        if spent() >= budget:
            print(f"Budget of ${budget} reached (spent ${spent():.4f}), stopping before question {number}.")
            break
        question = item["q"]
        judge_this = use_judge and (not judge_only or number in judge_only)
        try:
            answer, tool_calls, _ = ask_agent(question)
            check = check_answer(question, answer, tool_calls, item["tools_any"], judge_this)
        except Exception as e:
            answer, tool_calls = None, []
            check = {"status": "FAIL", "fail": [f"crashed: {type(e).__name__}: {e}"], "warn": []}

        print(f"{number:>2}. {check['status']}  {question[:80]}   (running cost ${spent():.4f})")
        for line in check["fail"]:
            print(f"      FAIL: {line}")
        for line in check["warn"]:
            print(f"      warn: {line}")

        results.append({"n": number, "question": question, "answer": answer,
                        "tool_calls": tool_calls, "check": check})

    counts = {s: sum(r["check"]["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    print(f"\n{len(results)} questions: {counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail")
    print(f"Estimated cost: ${spent():.4f} (budget ${budget})")

    with open("test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved test_results.json (full answers and tool outputs)")


if __name__ == "__main__":
    main()
