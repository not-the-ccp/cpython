#!/usr/bin/env python3
"""Ergonomic experiment for the pipeline expression (Misc/PIPELINE_EXPERIMENT.md).

This is an experiment-only script, not a standard-library API.  It exercises
the ``value |> body`` pipeline operator against real Python APIs, keeping both
a conventional (temporary-variable) version and a pipeline version of each
example where useful, and asserting that the two agree.

The goal is to expose awkward syntax, precedence surprises, and APIs where
piping is not actually clearer.  Observations for design review are recorded
at the end of each section as comments.  Run with:

    ./python Misc/pipeline_experiment.py
"""

import asyncio
import functools
import itertools
import json
import pathlib
import re

OBSERVATIONS = []


def observe(section, text):
    OBSERVATIONS.append((section, text))
    print(f"  [observe] {text}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# pathlib.Path
# ---------------------------------------------------------------------------

def pathlib_example():
    section("pathlib.Path")

    # Conventional:
    p = pathlib.Path("/home/user/docs/report.txt")
    conv = p.parent / p.name.replace(".txt", ".md")

    # Pipeline:
    piped = (pathlib.Path("/home/user/docs/report.txt")
             |> ($.parent / $.name.replace(".txt", ".md")))
    assert conv == piped, (conv, piped)
    print("  ", piped)

    # A common "walk and filter" idiom:
    conv2 = sorted(
        p.name for p in pathlib.Path(".").iterdir() if p.suffix == ".py"
    )
    piped2 = (pathlib.Path(".")
              |> (lambda d: sorted(p.name for p in d.iterdir()
                                   if p.suffix == ".py"))($))
    assert conv2 == piped2
    observe("pathlib", "path operations chain with '$.' method access, but "
            "comprehension-like steps still need a lambda wrapper because the "
            "pipe body is a single expression, not a statement block.")


# ---------------------------------------------------------------------------
# json
# ---------------------------------------------------------------------------

def json_example():
    section("json.loads / json.dumps")

    raw = '{"users": [{"name": "ada", "age": 36}, {"name": "lin", "age": 44}]}'

    # Conventional:
    data = json.loads(raw)
    conv = [u["name"] for u in data["users"]]
    conv2 = json.dumps({"count": len(data["users"])})

    # Pipeline:
    names = (raw |> json.loads($) |> ($["users"])
             |> (lambda us: [u["name"] for u in us])($))
    assert conv == names
    dumps = (raw |> json.loads($)
             |> (lambda d: json.dumps({"count": len(d["users"])}))($))
    assert conv2 == dumps
    print("  ", names, dumps)

    observe("json", "loading plus a subscript/comprehension is two operations; "
            "the pipe body can only hold one expression, so the whole step must "
            "be wrapped in a lambda.  A single 'load then project' step is "
            "clearer with a pipe only when the projection is a call.")


# ---------------------------------------------------------------------------
# re.sub
# ---------------------------------------------------------------------------

def re_example():
    section("re.sub")

    text = "Order 123 was placed on 2026-01-02."

    # Conventional:
    conv = re.sub(r"\d{4}-\d{2}-\d{2}", "[date]", text)

    # Pipeline:
    piped = text |> re.sub(r"\d{4}-\d{2}-\d{2}", "[date]", $)
    assert conv == piped
    print("  ", piped)

    # A two-stage transformation:
    conv2 = re.sub(r"\s+", " ", re.sub(r"\d+", "#", text)).strip()
    piped2 = (text |> re.sub(r"\d+", "#", $)
              |> re.sub(r"\s+", " ", $).strip())
    assert conv2 == piped2
    print("  ", piped2)

    observe("re", "this is the clearest fit for the operator: the replacement "
            "argument is naturally last and the stages read left to right.")


# ---------------------------------------------------------------------------
# map / filter / sorted / list
# ---------------------------------------------------------------------------

def builtin_example():
    section("map / filter / sorted / list")

    nums = [3, 1, 4, 1, 5, 9, 2, 6]

    # Conventional:
    conv = list(map(lambda n: n * n, filter(lambda n: n % 2, sorted(nums))))

    # Pipeline:
    piped = (nums
             |> sorted($)
             |> filter(lambda n: n % 2, $)
             |> map(lambda n: n * n, $)
             |> list($))
    assert conv == piped
    print("  ", piped)

    observe("map/filter/sorted", "each stage is a clean call with the topic "
            "last; this is the strongest fit, though long chains of "
            "lambda stages start to look like a list comprehension in "
            "disguise.")


# ---------------------------------------------------------------------------
# itertools
# ---------------------------------------------------------------------------

def itertools_example():
    section("itertools")

    pairs = [("a", 1), ("b", 2), ("a", 3)]

    # Conventional:
    conv = list(itertools.chain.from_iterable(
        re.sub(r"[^a-z]", "", k) + str(v) for k, v in pairs
    ))

    # Pipeline:
    piped = (pairs
             |> (lambda items: itertools.chain.from_iterable(
                 re.sub(r"[^a-z]", "", k) + str(v) for k, v in items))($))
    assert conv == list(piped)

    observe("itertools", "the topic is often the LAST argument of the "
            "itertools call, which fits; but generator-expression bodies "
            "inside the pipe still need full grouping, which reads worse than "
            "the conventional form.")


# ---------------------------------------------------------------------------
# methods and free functions in the same chain
# ---------------------------------------------------------------------------

def mixed_example():
    section("methods and free functions in one chain")

    raw = "  MiXeD  CaSe  "

    # Conventional:
    s = raw.strip()
    s = s.replace(" ", "_")
    s = s.title()
    conv = len(s)

    # Pipeline:
    piped = (raw
             |> $.strip()
             |> $.replace(" ", "_")
             |> $.title()
             |> len($))
    assert conv == piped
    print("  ", piped)

    observe("mixed", "mixing bound methods ($.m()) and free calls (len($)) in "
            "one chain reads naturally; no special syntax is needed.")


# ---------------------------------------------------------------------------
# async stages
# ---------------------------------------------------------------------------

async def fetch(name):
    await asyncio.sleep(0)
    return f"data:{name}"


def async_example():
    section("async stages")

    async def conv_main():
        x = fetch("a")
        x = await x
        return x.upper()

    async def piped_main():
        return "a" |> (await fetch($)) |> ($.upper())

    assert asyncio.run(conv_main()) == asyncio.run(piped_main())

    observe("async", "'await f($)' works inside a pipe body because the body "
            "is a full expression; the awaited result becomes the next stage's "
            "topic.")


# ---------------------------------------------------------------------------
# *args / **kwargs
# ---------------------------------------------------------------------------

def unpack_example():
    section("*args / **kwargs")

    args = (2, 10)
    conv = pow(*args)

    piped = (args |> pow(*$))
    assert conv == piped
    print("  ", piped)

    kwargs = {"base": 16}
    conv2 = int("ff", **kwargs)
    piped2 = (kwargs |> int("ff", **$))
    assert conv2 == piped2

    observe("unpack", "positional and keyword unpacking of the topic work as "
            "written; the topic expands exactly where a value would be "
            "written.")


# ---------------------------------------------------------------------------
# topic used twice in one stage
# ---------------------------------------------------------------------------

def double_use_example():
    section("topic used twice in one stage")

    def spread(values):
        lo, hi = min(values), max(values)
        return hi - lo

    nums = [4, 9, 1, 7]
    conv = spread(nums)
    piped = nums |> spread($)
    assert conv == piped

    # A simpler direct double use:
    x = 5
    conv2 = (x, x * x)
    piped2 = (x |> ($, $ * $))
    assert conv2 == piped2
    print("  ", piped2)

    observe("double-use", "the LHS is evaluated once and '$' may appear any "
            "number of times in the body; no re-binding is needed.")


# ---------------------------------------------------------------------------
# conditional stage
# ---------------------------------------------------------------------------

def conditional_example():
    section("conditional stage")

    def normalize(value, threshold):
        return "big" if value > threshold else "small"

    conv = normalize(42, 10)
    piped = (42 |> (lambda v: "big" if v > 10 else "small")($))
    assert conv == piped
    print("  ", piped)

    observe("conditional", "a conditional as a stage body needs grouping "
            "(or a lambda) because '|>' binds looser than the conditional; "
            "'x |> ($ if c else $)' is the natural spelling.")


# ---------------------------------------------------------------------------
# explicitly constructed functools.partial
# ---------------------------------------------------------------------------

def partial_example():
    section("explicit functools.partial")

    def combine(base, *additions):
        return base + sum(additions)

    partial = functools.partial(combine, 100)
    conv = partial(1, 2, 3)

    # The pipe does NOT create a partial; an explicitly constructed partial
    # is just a callable whose remaining argument is the topic.
    piped = (1, 2, 3) |> partial(*$)
    assert conv == piped
    print("  ", piped)

    observe("partial", "an explicit partial is used exactly as a normal "
            "callable; the pipe adds no partial-application magic of its own.")


def main():
    pathlib_example()
    json_example()
    re_example()
    builtin_example()
    itertools_example()
    mixed_example()
    async_example()
    unpack_example()
    double_use_example()
    conditional_example()
    partial_example()

    print("\n=== Recorded observations for design review ===")
    for section_name, text in OBSERVATIONS:
        print(f"  [{section_name}] {text}")
    print("\nAll pipeline and conventional forms agreed.")


if __name__ == "__main__":
    main()