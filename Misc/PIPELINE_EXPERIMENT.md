# CPython 3.14 pipeline-expression experiment

Status: **design-frozen for the reference experiment**

Baseline: upstream CPython `v3.14.7`, commit `823f0323ee6ec1402088b73bce1a38473cac36dc`.

This document is the semantic contract for the implementation. An implementation agent may make mechanical choices required by the CPython codebase, but must not redesign the language feature to make the implementation easier. If a requirement turns out to be technically problematic, stop and report the conflict rather than silently changing semantics.

## Decision

Implement a **topic pipeline expression**, not implicit partial application and not first/last-argument injection.

The syntax is:

```python
value |> body_using_$
```

`$` is the **pipe topic**. It denotes the value produced by the left-hand side of the immediately enclosing pipe step.

Examples:

```python
"hello" |> len($)                         # 5
"hello" |> len($) |> hex($)               # "0x5"

text |> re.sub(pattern, replacement, $)    # arbitrary argument position
xs |> map(transform, $) |> list($)          # iterable-last APIs
obj |> $.method()                           # method call
x |> $ + 1                                  # arbitrary expression
x |> ($, f($))                              # multiple topic uses are allowed
args |> f(*$)                               # ordinary * unpacking
kwargs |> f(**$)                            # ordinary ** unpacking
request |> await send($)                    # in an async function
name |> f"hello, {$}!"                      # topic is an expression
```

A pipe body **must contain at least one topic reference bound to that pipe step**. Therefore these are errors:

```python
x |> f()       # error: no topic; no implicit injection
x |> f         # error: no topic; no implicit unary call
$              # error: topic outside a pipe body
```

This explicitness is intentional. There is exactly one semantic mode.

## Why this design

The alternatives were investigated and rejected for the experiment:

* **Original bind/partial semantics** (`x |> f` returning something like `partial(f, x)`) conflate pipeline evaluation with partial-function construction. They also interact badly with ordinary call parsing. For example, under ordinary syntax `(4 |> add)()` would call the one-argument partial before an outer pipeline could provide its remaining argument. Python 3.14 already has `functools.partial` plus `functools.Placeholder` for partial application.
* **F#/Julia unary application** (`x |> f` means `f(x)`) is terse but handles only unary-call-shaped stages without wrapping more general operations.
* **Elixir/R-style first-argument injection** works well only when APIs consistently put the data argument first. Python does not: `sorted(xs, ...)`, `map(f, xs)`, and `re.sub(pattern, repl, text)` put the transformed value in different positions.
* **Thread-first/thread-last pairs** such as Clojure's `->`/`->>` solve two positions but still need a general named/topic form for mixed APIs. Adding multiple operators is strictly less general than one explicit topic.
* **Smart-mix semantics** (implicit injection when `$` is absent, explicit topic otherwise) have two modes, make omission of the topic silently meaningful, and make the meaning depend on RHS syntax. Do not implement them.
* **`_` as topic** hijacks an ordinary Python identifier used by gettext, interactive sessions, throwaway names, and even the documented `functools.Placeholder as _` idiom.
* **`?` as topic** would consume syntax space already explored by deferred PEP 505 (`??`, `?.`, `?[]`).
* `%`, `^`, and `@` already have strong Python operator meanings and become especially ugly when the body uses modulo, XOR, or matrix multiplication.
* `$` is new punctuation, but it is unambiguous, cannot collide with an ordinary binding, and reads tolerably in all required positions (`f($)`, `$.x`, `*$`, `**$`, `$ + 1`). For this experiment, use `$`. Do not bikeshed the token during implementation.

The design is closest in spirit to Hack/TC39 topic pipelines, adapted to Python's grammar and scoping conventions.

## Formal evaluation semantics

For one pipe step:

```python
LHS |> BODY
```

1. Evaluate `LHS` exactly once.
2. Bind the resulting object to this pipe step's implicit, unnameable topic binding.
3. Evaluate `BODY` normally, with every `$` lexically bound to that topic.
4. The value of `BODY` is the value of the complete pipe expression.

The important consequences are:

* LHS evaluation happens before any evaluation belonging to the body.
* A topic lookup does not copy or coerce the value.
* Normal Python left-to-right evaluation and short-circuit behavior applies inside the body.
* If LHS raises, the body is not evaluated.
* If the body raises, later pipeline stages are not evaluated.
* A chain is left-associative: `a |> f($) |> g($)` first finishes `f`, then binds its result as the next stage's topic for `g`.
* There is no `__pipe__`, `__rpipe__`, or other overload protocol. This is a language evaluation/binding construct, not user-defined binary dispatch.

Evaluation-order test that must pass:

```python
trace = []

def lhs():
    trace.append("lhs")
    return 10

def getf():
    trace.append("getf")
    return lambda a, b: a + b

def arg():
    trace.append("arg")
    return 2

assert lhs() |> getf()(arg(), $) == 12
assert trace == ["lhs", "getf", "arg"]
```

Do **not** implement the feature by simply moving the LHS AST into a call argument: that can reverse observable evaluation order.

## Topic scope and nested pipes

Each pipe body introduces its own topic binding. A nested pipe shadows the outer topic **only in the nested pipe's body**. Its LHS is still evaluated in the enclosing topic context.

Thus:

```python
x |> ($ |> f($))
```

is valid. The `$` used as the inner LHS denotes the outer topic; the `$` in `f($)` denotes the inner topic.

Conversely:

```python
x |> (y |> f($))
```

is invalid as an outer pipe, because the only `$` belongs to the inner pipe. The outer body never references its own topic.

A body may reference its topic more than once:

```python
x |> ($, $, f($))
```

The LHS is still evaluated once.

Topic references inside nested Python scopes use normal Python binding/closure behavior for the compiler-generated binding. In particular, this experiment deliberately follows Python's usual late-binding model rather than inventing value-capturing closure semantics. A nested lambda may capture the topic. Re-executing the same syntactic pipe site in the same enclosing frame may update what such a closure sees, analogous to a normal loop/local variable. A normal default-argument snapshot remains available when a snapshot is wanted.

Do not expose `$` as an assignable target. `$ = ...`, `del $`, and walrus assignment to `$` are invalid.

## Precedence and grammar

`|>` is lower precedence than `or` and higher precedence than the conditional expression and `lambda`.

Conceptually the relevant precedence slice is:

```text
:=                  lower
lambda
if ... else
|>                  NEW
or
and
not
comparisons
...
primary/call        higher
```

This is deliberate: normal arithmetic/boolean expressions form natural pipe heads and bodies, while low-precedence constructs use parentheses when they are intended as a stage body.

Required grouping examples:

```python
a + b |> f($)               # (a + b) |> f($)
x or y |> f($)              # (x or y) |> f($)
x |> f($) + 1               # x |> (f($) + 1)

# Conditional as the result of the whole expression:
x |> f($) if cond else y
# == (x |> f($)) if cond else y

# Conditional INSIDE a pipe body: parentheses required.
x |> (f($) if cond else g($))

# Whole conditional used as pipe head: parentheses required.
(x if cond else y) |> f($)

# Lambda body may naturally contain a pipeline.
fn = lambda x: x |> f($) |> g($)

# Lambda used as a pipe body must be grouped.
x |> (lambda: $)
```

Implement this by inserting a left-recursive `pipeline` rule between `expression` and `disjunction`, not by giving `|>` high arithmetic precedence.

Target grammar shape (adapt to exact PEG invalid-rule conventions without changing grouping):

```peg
expression[expr_ty] (memo):
    | invalid_expression
    | invalid_legacy_expression
    | a=pipeline 'if' b=disjunction 'else' c=expression { _PyAST_IfExp(b, a, c, EXTRA) }
    | pipeline
    | lambdef

pipeline[expr_ty]:
    | a=pipeline '|>' b=disjunction { _PyAST_Pipeline(a, b, EXTRA) }
    | disjunction
```

Add `$` as an atom producing `PipeTopic`.

Low-precedence Python grammar slots that currently accept `disjunction` rather than full `expression` may consequently require parentheses around a pipeline. Do not widen unrelated grammar rules just to avoid parentheses; record any especially awkward case for later design review.

## Partial application remains separate

Do not make `|>` construct bound callables.

Python 3.14 already has the correct explicit abstraction:

```python
from functools import partial, Placeholder

add4 = partial(lambda a, b: a + b, 4)
assert 3 |> add4($) == 7

# The original "bind the current value" capability remains expressible,
# but it says partial explicitly:
bound_len = "hello" |> partial(len, $)
assert bound_len() == 5
```

Arbitrary positional partial holes remain the job of `functools.Placeholder`. Do not add another partial-application operator as part of this experiment.

## Unpacking needs no pipe variant

The topic is an ordinary expression, so existing Python call syntax already solves iterable/mapping expansion:

```python
args |> f(*$)
kwargs |> f(**$)
pair |> f(*$[0], **$[1])
```

Do not add `|>>`, `|*>`, `|**>`, a reverse pipe, or any other insertion/splat operator.

## No implicit call mode

These stay errors:

```python
x |> f
x |> f()
x |> f(1, 2)
```

The explicit forms are:

```python
x |> f($)
x |> f($, 1, 2)
x |> f(1, $, 2)
x |> f(1, 2, $)
```

This one-character tax on the common unary-call case is intentional. It buys a single semantic law, arbitrary argument placement, and catches forgotten data flow.

## No optional/None-aware pipeline

Do not add `|?>`, `?|>`, or short-circuit-on-`None` semantics. That is a separate language-design problem overlapping deferred PEP 505.

## No overload protocol

Do not add `__pipe__`, RHS source-code hooks, AST hooks, implicit lambda construction, or user-overridable stage semantics. Prior Python pipeline prototypes became dramatically more complicated once the operator was allowed to change meaning by type. The experiment is specifically about a predictable syntax-level data-flow construct.

## AST contract

Add two public AST expression node kinds:

```text
Pipeline(expr value, expr body)
PipeTopic
```

`Pipeline.value` is the left side. `Pipeline.body` is the RHS stage expression. Chained syntax is represented left-associatively by nested `Pipeline` nodes.

Example conceptual AST:

```python
x |> f($) |> g($)
```

becomes:

```text
Pipeline(
    value=Pipeline(
        value=Name("x"),
        body=Call(Name("f"), [PipeTopic()]),
    ),
    body=Call(Name("g"), [PipeTopic()]),
)
```

Do not encode this as `BinOp`; the RHS has a lexical topic and therefore is not an ordinary binary data-model operation.

`ast.unparse()` must round-trip the feature with correct precedence and parentheses.

## Compiler implementation strategy

Prefer direct code generation; do not allocate `functools.partial`, bound-method objects, synthetic Python functions, or tuples at runtime.

A straightforward reference implementation can model each syntactic `Pipeline` AST node with a compiler-generated, source-unspellable identifier. The name must be unique per `Pipeline` node in one compilation, e.g. based on the internal AST node address and prefixed with an invalid source identifier such as `".<pipe_topic_...>"`.

Use a shared helper so symtable and codegen derive the same hidden identifier for the same internal `Pipeline` node. Do not put the hidden identifier in the public AST.

### Symtable

Maintain a stack/current pointer for active pipeline topics.

For `Pipeline(value, body)`:

1. Visit `value` while the enclosing topic (if any) remains active.
2. Create/derive the hidden identifier for this Pipeline node and define it in the current Python scope.
3. Push it as the active topic.
4. Visit `body`.
5. Pop it.

For `PipeTopic()`:

* require an active topic (preprocessing should already have emitted a syntax error otherwise), and
* record a USE of the active hidden identifier in the current symbol-table entry.

Keep the topic context active while visiting nested lambdas/comprehensions so ordinary CPython free/cell-variable analysis can provide Python's normal closure behavior.

### Codegen

Maintain the same active-topic stack/current pointer.

For `Pipeline(value, body)`:

1. Compile `value`, leaving its result on the stack.
2. Store it into this Pipeline node's hidden topic binding using the same name/scope machinery as an ordinary Name store.
3. Push this topic as active.
4. Compile `body`; its resulting stack value is the Pipeline result.
5. Pop the topic.

For `PipeTopic()`:

* compile a load of the active hidden topic binding.

This gives correct evaluation order and avoids a special runtime object. Ordinary symbol resolution selects fast-local, cell/free, name, or global opcodes as appropriate.

The compiler-generated binding is an implementation detail. If practical, mark fast-local topic slots hidden using existing `u_fasthidden`/`CO_FAST_HIDDEN` mechanisms. Do not let hiding work block the semantic implementation. At module/class scope an impossible-name key may remain visible through deep introspection in the initial experiment; document that as a prototype artifact rather than changing user semantics around it.

Do not add a new public C API.

## Static validation

The AST preprocessing/validation path must maintain nested Pipeline contexts and enforce:

1. `PipeTopic` outside a Pipeline body is `SyntaxError`.
2. Every Pipeline body contains at least one `PipeTopic` lexically bound to that Pipeline.
3. A topic in a nested Pipeline body counts for the nested Pipeline only.
4. A topic in a nested Pipeline **value/LHS** still belongs to the enclosing Pipeline and counts for it.

Suggested messages:

```text
pipeline topic '$' is only valid in a pipeline body
pipeline body must reference '$'
```

Do the validation in a path that also runs for `ast.parse`/`PyCF_ONLY_AST`, so invalid source does not produce a supposedly valid AST.

## Tokenizer/parser changes

Add exact tokens for:

```text
VBARGREATER   '|>'
DOLLAR        '$'
```

Use CPython's established generated-token machinery. `Grammar/Tokens` explicitly reminds contributors to update `Tools/peg_generator/pegen/parser_generator.py` for new tokens so older Python versions can bootstrap the parser; do that.

`tokenize` should expose both spellings as operators with correct `exact_type` values, and untokenize/tokenize round-trips must work.

## Files expected to change

At minimum inspect/change the source-of-truth equivalents of:

* `Grammar/Tokens`
* `Grammar/python.gram`
* `Tools/peg_generator/pegen/parser_generator.py`
* `Parser/Python.asdl`
* `Python/ast_preprocess.c` (validation)
* `Python/symtable.c`
* `Python/codegen.c`
* `Python/ast_unparse.c`
* AST validation/conversion machinery as required by the generated ASDL change
* tokenizer/parser/AST generated outputs produced by regeneration
* `Lib/test/test_grammar.py` and/or a dedicated language test module
* `Lib/test/test_ast/` tests
* `Lib/test/test_tokenize.py`
* expression/compile/symtable tests where appropriate
* language-reference documentation for expressions and precedence once semantics pass

Do not hand-edit generated files when a regen target owns them.

Run `make regen-all` if uncertain which generated targets are needed; CPython's developer guide explicitly recommends this approach.

## Required semantic tests

The implementation is not done until all of these categories are covered.

### Basic

```python
assert ("hello" |> len($)) == 5
assert ("hello" |> len($) |> hex($)) == "0x5"
assert (10 |> $ + 1 |> $ * 2) == 22
assert ([1, 2, 3] |> $[1]) == 2
```

### Position

```python
assert ("abc123" |> re.sub(r"\d+", "#", $)) == "abc#"
assert ([1, 2, 3] |> map(lambda x: x + 1, $) |> list($)) == [2, 3, 4]
```

### Methods

```python
assert ("  hello " |> $.strip() |> $.upper()) == "HELLO"
```

### Multiple use and single LHS evaluation

```python
calls = 0

def source():
    global calls
    calls += 1
    return 5

assert (source() |> $ * $ + $) == 30
assert calls == 1
```

### `*` / `**`

```python
assert ((2, 3) |> pow(*$)) == 8
assert ({"base": 16} |> int("ff", **$)) == 255
```

### Evaluation order

Use the `lhs/getf/arg` trace example above and additional exception-order tests.

### Short circuit

```python
seen = []
assert (0 |> ($ and seen.append(1))) == 0
assert seen == []
assert (1 |> ($ and 7)) == 7
```

### Conditional precedence

Test AST and runtime grouping for all examples in the precedence section.

### Nested topics

Test valid outer-topic use in an inner LHS, nested-body shadowing, and the invalid outer-unused example.

### Lambdas/closures

Test topic use in a lambda created in a pipe body and explicitly test/document normal Python late-binding behavior on repeated execution of one syntactic pipe site. Also test the standard default-argument snapshot idiom.

### Comprehensions/generator expressions

Exercise topic use as an iterable, in element/filter expressions, and in a generator expression. Verify scope analysis and that no ordinary user variable leaks or changes binding.

### Async/generator

Inside suitable functions:

```python
x |> await f($)
x |> (yield $)
```

Verify suspension/resumption and exception behavior.

### Walrus

```python
result = value |> (saved := f($)) |> g($)
```

`saved` must use Python's existing named-expression scope rules; the pipeline must not introduce a Python-visible function scope.

### Syntax errors

Test `$` alone, topic-free pipe bodies, assignment to `$`, malformed `|>` token sequences, and nested-topic ownership.

### AST

* `ast.dump` contains `Pipeline` and `PipeTopic`.
* parsing/unparsing/reparsing preserves meaning.
* manually constructed valid ASTs compile.
* manually constructed invalid topic placement is rejected.
* location information is correct enough for PEP 657 traceback highlighting.

### Tokenize

Test token types/exact types, comments/newlines, f-string replacement fields, and round-trip untokenization.

## Broader regression tests

After focused tests:

```sh
./configure --with-pydebug
make -j"$(nproc)"
make regen-all
make -j"$(nproc)"
./python -m test test_grammar test_ast test_tokenize test_compile test_symtable test_dis
./python -m test -j2
```

Use the exact commands appropriate to the host if already configured. Do not install the interpreter system-wide.

No existing test may be weakened, skipped, or deleted to accommodate the feature.

## Ergonomic experiment program

After the implementation passes focused tests, add an **experiment-only** script (not a stdlib API) covering mixed real Python APIs:

* `pathlib.Path`
* `json.loads` / `json.dumps`
* `re.sub`
* `map`, `filter`, `sorted`, `list`
* `itertools`
* methods and free functions in the same chain
* `async` stages
* `*args` and `**kwargs`
* a stage using the topic twice
* a conditional stage
* an explicitly constructed `functools.partial`

Keep both nested/temporary-variable and pipeline versions where useful. The purpose is to expose awkward syntax, precedence surprises, and APIs where piping is not actually clearer. Do not alter the language design based on aesthetic guesses made by the implementation agent; record observations for review.

## Explicitly rejected extensions for this experiment

Do not add any of these unless the design document is deliberately revised first:

* `x |> f` implicit unary application
* `x |> f()` implicit first-argument injection
* implicit last-argument injection
* `|>>`, `<|`, `|*>`, `|**>`, or similar variants
* a magic `_` placeholder
* `?` topic syntax
* a pipe-created `functools.partial`
* a special bound-callable runtime type
* a prefix pipe/function-composition syntax
* `__pipe__` or operator overloading
* source-code/AST access from a pipe magic method
* None-aware/exception-aware pipe variants
* augmented assignment `|>=`

## What Qwen is allowed to decide

Qwen may decide only implementation details that do not affect observable semantics, such as helper-function placement, C naming consistent with nearby code, exact test-file organization, and which standard regen target is sufficient.

Qwen must **not** decide:

* whether `$` can be omitted,
* whether a call gets first/last injection,
* precedence,
* associativity,
* topic ownership in nested pipes,
* closure behavior,
* whether the operator is overloadable,
* whether partial application is implicit,
* whether another operator should be added,
* or whether a failing semantic test should be changed to match an easier implementation.

If codebase reality makes one of those requirements impossible or substantially more invasive than described, stop and report the exact conflict with file/line evidence.

## Research basis

Useful prior art reviewed for this design:

* F# pipeline operator: unary-function application.
* Elixir `|>`: first-argument insertion; `then/2` exists for awkward positions.
* Clojure `->`, `->>`, and `as->`: first, last, and explicit arbitrary-position threading.
* R native `|>`: first-argument insertion plus a restricted `_` placeholder.
* Hack pipeline syntax: lexical explicit topic.
* TC39 pipeline proposal: currently topic-pipeline based; its rationale explicitly compares topic vs F# semantics and keeps partial application separate.
* Python PEP 309 and Python 3.14 `functools.Placeholder`: partial application is already a distinct Python abstraction.
* Deferred PEP 505: reason not to consume `?` for the topic.
* 2024-2025 Python.org discussions about `functools.partial` placeholders and proposed funnel/pipeline operators, including the practical failure of a universal first/last insertion position.
* The existing `sadaszewski/cpython-pipeline-syntax` prototype: useful evidence that `_` rewriting, implicit call injection, `__pipe__`, source-code hooks, and AST-wide rewriting rapidly make the construct context-sensitive and difficult to reason about. Those parts are intentionally not copied.

The goal of this branch is a clean experiment with one semantic law, not a claim that CPython upstream would accept the syntax.