# CPython `|>` experiment: settled design and implementation handoff

Target: CPython 3.14.7, branch `pipe-topic-3.14.7`.

This document is normative for the experiment. If implementation details conflict with this document, stop and report the conflict rather than changing the language design.

## 1. Goal

Add a forward pipeline syntax that linearizes ordinary Python function calls without introducing implicit iteration, partial application, user-overloadable pipe protocols, or a family of directional pipe operators.

The feature is intentionally a **call-stage pipe**, not an F#/Julia-style bare-call operator and not a partial-application operator.

Canonical examples:

```python
"hello" |> len()                     # 5
"hello" |> len() |> hex()            # "0x5"
3 |> (lambda a, b: a + b)(4)         # 7

xs |> sorted(key=keyfunc) |> list()
xs |> filter(pred, ...) |> map(f, ...) |> list()
text |> re.sub(pattern, replacement, ...)
parts |> " ".join()
```

## 2. Explicitly rejected designs

Do **not** implement any of these unless this design document is deliberately revised first:

- `x |> f` returning `partial(f, x)` or another bound callable. Pipe and partial application are separate features.
- `x |> f` silently calling `f(x)`. Bare callable application is deliberately not part of this experiment; Python normally makes calls visually explicit with `()`.
- `__pipe__`, `__rpipe__`, `__pipe_iter__`, or any other overload protocol.
- Implicit `iter(x)` / `aiter(x)` conversion.
- A second operator for last/second/nth argument insertion (`|>>`, etc.).
- Separate splat-pipe operators. `*...` and `**...` already cover these cases.
- A general RHS topic-expression mini-language such as `x |> ... + 1`.
- Reusing `_` as a placeholder. `_` is an ordinary Python name with entrenched uses.
- Using `?` as the pipe placeholder. It consumes syntax space relevant to deferred None-aware syntax and would require a new placeholder token for little benefit.
- Optional/null pipes, tap/debug pipes, concurrency/scheduler semantics, stream fusion, or pipeline introspection.
- `|>=` augmented assignment.
- New pipe-specific bytecode or heap-allocated pipe/partial wrapper objects unless profiling later proves ordinary CALL machinery insufficient.

## 3. Syntax and semantics

### 3.1 Stage shape

A pipe is left-associative:

```python
head |> stage1(...) |> stage2(...)
```

Each RHS stage must syntactically be an ordinary call expression, or an awaited ordinary call expression. The outermost expression of a non-awaited stage must be `Call`; for an awaited stage it must be `Await(Call(...))`.

Invalid:

```python
x |> f
x |> 123
x |> a + b
x |> obj.attr
```

Valid:

```python
x |> f()
x |> f(a, b)
x |> obj.method(a)
x |> (f if cond else g)()
x |> (lambda y: y + 1)()
x |> factory()()
x |> await async_f()
```

A targeted syntax error should be produced for the common `x |> f` mistake, approximately:

```text
pipe target must be a function call; write f() rather than f
```

Exact wording may follow CPython diagnostic conventions.

### 3.2 Default insertion

If the outermost RHS call has no pipe placeholders, the already-evaluated LHS value is inserted as the first positional argument:

```python
x |> f()              # f(x)
x |> f(a)             # f(x, a)
x |> f(a, k=v)        # f(x, a, k=v)
x |> f(*args)         # f(x, *args)
x |> f(**kwargs)      # f(x, **kwargs)
```

This default is chosen because first-position data arguments dominate Python APIs that naturally act as pipeline stages, while the placeholder below covers the substantial minority using another position.

### 3.3 Explicit placeholder: direct `...`

Within a pipe stage only, a direct Ellipsis argument of the **outermost RHS call** is a reference to the pipe topic. If at least one such placeholder occurs, default first-argument insertion is suppressed.

```python
x |> f(a, ...)        # f(a, x)
x |> f(..., a)        # f(x, a)
x |> f(data=...)      # f(data=x)
xs |> map(f, ...)      # map(f, xs)
text |> re.sub(p, r, ...)  # re.sub(p, r, text)
```

Multiple direct placeholders are allowed. The LHS is evaluated once and the same resulting object is reused:

```python
x |> f(..., ...)       # evaluate x once; then f(x, x)
x |> f(..., key=...)  # f(x, key=x)
```

### 3.4 Positional and keyword unpacking

A direct placeholder underneath an outermost `*` or `**` is also special:

```python
args |> f(*...)        # f(*args)
kwargs |> f(**...)     # f(**kwargs)
```

This uses ordinary Python call semantics. If the topic is not iterable/mapping as required, the normal call error is raised. Multiple occurrences are allowed and ordinary duplicate-key rules apply.

No separate varargs pipe operator is needed.

### 3.5 Ellipsis is contextual only in direct outer call slots

Do not recursively search arbitrary RHS expressions for Ellipsis. Only these shapes are placeholders:

- an outermost positional argument exactly equal to `...`;
- an outermost starred positional argument exactly `*...`;
- an outermost named keyword value exactly `...`;
- an outermost double-star keyword value exactly `**...`.

Nested Ellipsis remains the ordinary Ellipsis literal:

```python
x |> f([..., 1])      # f(x, [Ellipsis, 1])
x |> f((...,))        # f(x, (Ellipsis,))
x |> f(g(...))        # f(x, g(Ellipsis))
```

If a literal Ellipsis is required as a direct outer argument, spell it `Ellipsis` (or otherwise produce it with an ordinary expression):

```python
x |> f(Ellipsis)      # f(x, Ellipsis)
```

The fact that the built-in name `Ellipsis` can technically be shadowed is accepted; exact literal identity can be obtained through a nested expression or `builtins.Ellipsis` when needed.

### 3.6 Outermost-call rule

Injection belongs to the outermost RHS call, not the first call textually encountered:

```python
x |> factory()()      # factory()(x)
x |> f().g()          # f().g(x)
```

To pipe into `f` and then continue with an ordinary trailer, parenthesize the pipe result:

```python
(x |> f()).g()        # f(x).g()
(x |> f())[0]         # f(x)[0]
```

This rule follows the Python AST's ordinary notion of what the call expression actually is and avoids a special search for a "leftmost" call.

### 3.7 Evaluation order

This is semantically important and must be tested explicitly.

For:

```python
lhs() |> callee()(arg1(), arg2())
```

evaluate in this order:

1. `lhs()` exactly once;
2. the RHS callee expression (`callee()` here);
3. RHS argument expressions in normal Python call order;
4. perform the call.

The LHS value is retained internally and inserted/reused without reevaluation.

Consequences:

- an exception in the LHS prevents any RHS evaluation;
- an exception evaluating the RHS callable prevents argument evaluation as in an ordinary call;
- multiple placeholders do not repeat the LHS side effect;
- each stage finishes before the next stage begins.

A parser-only rewrite from `lhs |> f(arg)` to `f(lhs, arg)` is **not** semantically sufficient in Python because an ordinary call evaluates its callable before its arguments, which would move evaluation of `f` before the original LHS. Preserve pipe-head-first evaluation in code generation.

### 3.8 Async

Allow an awaited call stage:

```python
value |> await transform_async()
value |> await transform_async() |> normalize()
```

Semantics are equivalent in value/result terms to awaiting a call with the topic inserted, while still preserving pipe-head-first evaluation:

```python
await transform_async(value)
```

`await` legality follows existing Python rules; outside a valid async context it must fail as normal. No special async-iterator conversion is performed.

`await value |> f()` groups as `(await value) |> f()` because ordinary `await` binds within the LHS expression. `await (value |> f())` remains ordinary explicit awaiting of the entire pipe expression.

### 3.9 No runtime overloading

`|>` is syntax for this call behavior. It does not invoke a dunder method on either operand. Defining methods named `__pipe__` or similar has no effect.

## 4. Precedence and associativity

Use a new precedence level **lower than bitwise OR (`|`) and higher than comparisons**.

Therefore:

```python
a + b |> f()          # (a + b) |> f()
a | b |> f()          # (a | b) |> f()
a |> f() == b         # (a |> f()) == b
a == b |> f()         # a == (b |> f())
not x |> f()           # not (x |> f())
x |> f() and y         # (x |> f()) and y
```

The operator is left-associative:

```python
x |> f() |> g()       # (x |> f()) |> g()
```

For arithmetic or trailers applied *after* a pipe result, use grouping when necessary:

```python
(x |> f()) + 1
(x |> f()).attr
(x |> f())[0]
```

Multiline use should work naturally inside parentheses:

```python
result = (
    source
    |> normalize()
    |> filter(valid, ...)
    |> map(convert, ...)
    |> list()
)
```

## 5. Why this design

Research considered F#/OCaml/Julia-style `x |> f == f(x)`, R and Elixir call-stage pipes, Clojure's first/last threading split, the TC39 pipeline work, Python's 2024-2025 funnel-operator discussion, and Python 3.14 `functools.Placeholder`.

The important conclusions are:

- The original idea of making `x |> f` produce a partial conflates pipelines with partial application. Other language-design work repeatedly treats those as independent features, and Python 3.14 already has `functools.partial` plus positional `Placeholder`.
- Bare F# application is beautifully small, and is likely the most defensible design if upstream CPython accepts only a minimal pipe. However Python is not conventionally curried and ordinary Python calls are visually explicit. It also leaves `map`, `filter`, `reduce`, several regex APIs, and many scientific APIs needing lambdas/partials.
- A family of first/last/second pipe operators does not solve arbitrary argument placement. Real Python APIs need first, second, third, keyword, `*`, and `**` positions.
- A fully general Hack/TC39 topic-expression language is powerful but creates a new mini-language and much wider grammar/scope questions than are justified for this experiment.
- A call-only RHS plus first-argument default and one direct call-slot placeholder gets most of the useful generality without allowing arbitrary hidden topic references in expressions or nested scopes.
- `...` is preferable here to `_` or a new `?` token because it already lexes as a single token, visually means a hole/omission, and composes naturally with Python's existing star-call syntax.

If an eventual upstream PEP is the goal, preserve the option to present a smaller `x |> callable` proposal separately. Do not distort this experiment merely to predict upstream acceptance; this branch is intended to test the more ergonomic call-stage design honestly.

## 6. AST design

Add a dedicated expression node rather than lowering immediately to ordinary `Call`:

```text
PipeCall(expr value, expr func, expr* args, keyword* keywords)
```

Recommended ASDL placement: near `Call`.

Examples conceptually:

```python
x |> f()
# PipeCall(value=x, func=f, args=[], keywords=[])

x |> f(a, ...)
# PipeCall(value=x, func=f, args=[a, Constant(Ellipsis)], keywords=[])

x |> f(data=...)
# PipeCall(value=x, func=f, args=[], keywords=[keyword('data', Constant(Ellipsis))])
```

Direct `Constant(Ellipsis)` in the PipeCall's own call slots is contextual and means topic placeholder. Nested constants are ordinary Ellipsis.

Do **not** represent the whole construct as an ordinary `BinOp`, and do not lower it to `Call` in the parser. The dedicated node is needed to preserve pipe-head-first evaluation order through later compiler stages.

Required AST integration includes validation, symbol-table traversal, optimizer traversal, generic AST generation/regeneration, `ast.dump`, `ast.unparse`, AST equality/round-trip behavior, and source locations.

The optimizer may optimize child expressions normally but must not rewrite `PipeCall` to a plain `Call` if doing so changes evaluation order.

## 7. Parser design

### 7.1 Token

Add an exact token for `|>` (suggested name `PIPE`) in `Grammar/Tokens` and regenerate token/parser outputs. Follow the repository's note about updating/generated PEG token knowledge.

No `|>=` token.

### 7.2 Grammar layer

Insert a `pipe_expr` layer between `bitwise_or` and `comparison`.

Schematic grammar only; adapt names to CPython style:

```text
comparison:
    | a=pipe_expr b=compare_op_pipe_expr_pair+ { ... }
    | pipe_expr

pipe_expr:
    | a=pipe_expr '|>' b=await_primary { build_pipe_stage(a, b) }
    | bitwise_or
```

Comparison helper productions currently consuming `bitwise_or` must consume `pipe_expr` after this change.

The builder accepts:

- `Call(...)` -> `PipeCall(lhs, call.func, call.args, call.keywords)`
- `Await(Call(...))` -> `Await(PipeCall(lhs, ...))`

and rejects other RHS shapes with a targeted syntax error.

A parser helper in `Parser/action_helpers.c/.h` is preferable if it keeps the grammar action readable.

Do not recursively rewrite nested calls and do not scan nested expressions for placeholders.

### 7.3 Locations

Preserve useful positions:

- `PipeCall` should span from the beginning of the LHS through the end of the RHS call.
- RHS func/args retain their original positions.
- Awaited stages should give tracebacks/position-aware disassembly sensible locations consistent with existing `Await` behavior.

Add exact location tests, including multiline stages.

## 8. Compiler/codegen design

No wrapper object and no `functools.partial` call at runtime. Compile a PipeCall directly using existing call bytecode and stack manipulation.

### 8.1 Fast path: no `*` / `**`

The essential stack plan is straightforward with CPython 3.14 `COPY` and `SWAP`.

After evaluating `value`, then `func`, then `PUSH_NULL`, the stack is conceptually:

```text
[value, func, NULL]
```

Use:

```text
SWAP 2
SWAP 3
```

to obtain:

```text
[func, NULL, value]
```

For a stage with **no explicit placeholder**, that `value` is simply the first argument; compile the ordinary explicit args/kwargs after it and emit `CALL`/`CALL_KW` with the increased argument count.

For a stage **with explicit placeholders**, treat the top `value` as a carrier while arguments are emitted below it:

- ordinary positional/keyword value: compile it, then `SWAP 2`, leaving emitted argument below the carrier;
- direct placeholder: `COPY 1`, leaving one copy as the emitted argument and one as the carrier;
- after all argument values are emitted, `POP_TOP` the carrier;
- emit normal keyword-name tuple / `CALL_KW` machinery as appropriate.

This naturally supports multiple placeholders while evaluating the LHS once.

Validate the exact stack effects against generated bytecode metadata; the conceptual ordering above is normative, not an excuse to bypass CPython's stack verifier.

### 8.2 Calls involving `*` or `**`

Adapt the existing `codegen_call_helper_impl`, star-unpack, and keyword-unpack machinery rather than inventing a runtime pipe object.

A good stack strategy is to keep the pipe topic as a carrier while constructing the positional list/tuple and keyword mapping, duplicating it with `COPY(depth)` when a `...`, `*...`, or `**...` slot needs it. The compiler statically knows the temporary stack depth. Remove the carrier before `CALL_FUNCTION_EX`.

Preserve all ordinary call errors and ordering, including:

- non-iterable `*` values;
- non-mapping `**` values;
- duplicate keyword errors;
- source-order evaluation of unpack expressions.

Do not silently fall back to creating a lambda, partial, tuple-of-everything wrapper object, or hidden Python local. A compiler-internal stack carrier is preferred because it does not leak into `locals()`, `co_varnames`, tracing, or introspection.

If adapting the helper requires a mechanical helper parameter such as an injected stack value/depth, that is fine. If the implementation seems to require changing language semantics, stop and report.

### 8.3 Method-call optimization

Correctness first. It is acceptable for the first compiling implementation of PipeCall to use the generic callable + NULL call path rather than immediately reproducing `maybe_optimize_method_call`/`LOAD_METHOD` optimizations.

After correctness tests pass, inspect disassembly and benchmark. Add a method-call optimization only if it is straightforward and demonstrably useful; do not complicate semantics for it.

### 8.4 No new opcode initially

Existing `CALL`, `CALL_KW`, `CALL_FUNCTION_EX`, `COPY`, `SWAP`, list/dict build helpers, and intrinsics should be sufficient. A new opcode requires evidence from profiling and is outside the initial implementation mandate.

## 9. Unparser and documentation

Add a PIPE precedence level to `_ast_unparse.py` between bitwise OR and comparisons.

Canonical unparse should produce call-stage syntax. It need not reproduce whether a semantically redundant first-position placeholder was explicit in the original source if the AST representation cannot preserve that cosmetic distinction, but round-tripping must preserve semantics.

Document:

- call-only RHS;
- first-argument default;
- direct `...`, `*...`, `**...` rules;
- multiple placeholders and single LHS evaluation;
- nested Ellipsis rule;
- outermost-call rule;
- precedence table;
- evaluation order;
- async form;
- explicit rejection of overload/iteration semantics.

## 10. Test matrix

Do not consider the feature complete merely because the three motivating examples work.

### Basic behavior

```python
assert ("hello" |> len()) == 5
assert ("hello" |> len() |> hex()) == "0x5"
assert (3 |> (lambda a, b: a + b)(4)) == 7
```

Test positional args, keyword args, defaults, bound methods, builtins, Python functions, callable instances, C callables, lambdas, and conditional callable expressions.

### Position examples

```python
range(10) |> filter(pred, ...) |> list()
range(10) |> map(f, ...) |> list()
seq |> functools.reduce(f, ...)
text |> re.sub(pattern, repl, ...)
value |> f(..., a, b)
value |> f(a, ..., b)
value |> f(data=...)
```

### Placeholder reuse and nesting

```python
obj |> f(..., ...)
obj |> f(..., key=...)
obj |> f([..., 1])       # nested Ellipsis literal
obj |> f(g(...))         # nested Ellipsis literal passed to g
obj |> f(Ellipsis)       # direct literal escape
```

Verify object identity is preserved across repeated placeholder uses.

### Unpacking

```python
args |> f(*...)
kwargs |> f(**...)
args |> f(prefix, *...)
kwargs |> f(a=1, **...)
```

Also combine ordinary `*other`/`**other` with topic placeholders and test duplicate-key behavior.

### Outermost call

```python
x |> factory()()
x |> f().g()
(x |> f()).g()
(x |> f())[0]
```

### Evaluation order

Use side-effecting helpers that append labels to a list. Verify exactly:

```text
lhs, callee, arg1, arg2, call
```

for an appropriate expression. Verify:

- LHS once with zero, one, and multiple placeholders;
- LHS exception prevents RHS evaluation;
- callee exception prevents arg evaluation;
- one stage completes before next stage begins;
- unpacking expressions keep ordinary order.

### Precedence

Cover arithmetic, matrix multiply, shifts, bitwise `& ^ |`, comparisons and comparison chains, `is`, `in`, `not`, `and`, `or`, conditional expressions, lambda bodies, walrus where legal, `await`, and parenthesized variants.

At minimum assert the intended grouping of:

```python
a + b |> f()
a | b |> f()
a |> f() == b
a == b |> f()
not x |> f()
x |> f() and y
```

### Async

Inside `async def`, test:

```python
x |> await coro()
x |> await coro() |> f()
await (x |> coro_returning_awaitable())
```

Also verify existing invalid-`await` diagnostics outside async contexts.

### Invalid syntax

Test targeted or stable SyntaxErrors for:

```python
x |> f
x |> 1
x |>
x |> await f
```

and malformed calls/placeholders. Existing Python call-argument grammar errors should remain familiar.

### AST and unparse

Test:

- `ast.parse` node shapes;
- `ast.dump`;
- AST validator acceptance/rejection;
- `ast.unparse` and parse/unparse/parse semantic equivalence;
- source locations/end locations;
- programmatically constructed PipeCall nodes;
- optimizer traversal.

### Bytecode / runtime

Use `dis` tests to ensure ordinary stages do not allocate partial/binding objects. Verify stack depth metadata and exception-table correctness.

### Regression

Run the relevant parser/AST/compiler/dis test modules, then the full CPython test suite in a debug build. Also run regen checks and repository patch checks expected by CPython development workflow.

## 11. Performance experiments

After correctness:

Benchmark equivalent functions written as:

```python
f(x)
g(f(x))
h(g(f(x)))
```

versus pipe forms with 1, 3, 10, and 20 stages. Include Python-callable and builtin/C-callable cases, plus placeholder and unpacking cases.

The goal is not zero overhead at any cost; the important constraints are:

- no per-stage partial/wrapper heap allocation;
- no hidden Python frame or lambda;
- simple stages should differ mainly by the small stack shuffling needed to preserve pipe-head-first evaluation order;
- complex unpacking should be comparable to ordinary `CALL_FUNCTION_EX` construction.

Report disassembly and benchmark numbers rather than guessing.

## 12. Likely files touched

At minimum inspect/touch the appropriate generated companions for changes in:

```text
Grammar/Tokens
Grammar/python.gram
Parser/Python.asdl
Parser/action_helpers.c
Parser/action_helpers.h
Python/ast.c
Python/ast_opt.c
Python/symtable.c
Python/codegen.c
Lib/_ast_unparse.py
Doc/reference/expressions.rst
Doc/library/ast.rst
Lib/test/test_grammar.py
Lib/test/test_ast/
Lib/test/test_dis.py (if appropriate)
```

Do not blindly edit generated files by hand when the CPython regen targets are authoritative. Let `make regen-*` / `make regen-all` reveal all generated outputs that belong in the commit.

## 13. Execution instructions for Qwen3.8

Qwen's role is implementation/build/test mechanics, **not language design**.

1. Confirm the checkout is exactly branch `pipe-topic-3.14.7` based on Python 3.14.7 and that the worktree is understood before modifying anything.
2. Read this entire document before editing.
3. Inspect the existing PEG, ASDL generation, AST visitors, symtable, optimizer, and call codegen before patching.
4. Implement the exact semantics above. Prefer small, idiomatic CPython changes over parallel custom machinery.
5. Regenerate all required generated sources using CPython's own targets.
6. Configure/build a debug interpreter and fix compiler errors mechanically.
7. Add the complete test matrix above, not just smoke tests.
8. Run focused tests repeatedly until clean, then broad/full tests.
9. Run CPython's patch/regen checks normally expected for a core change.
10. Produce disassembly and microbenchmark results.
11. Review the diff for accidental generated noise and unrelated formatting changes.
12. Commit in coherent commits and push only to `pipe-topic-3.14.7`; do not merge to `main` and do not open an upstream CPython PR.
13. Leave a report containing exact commands, test results, benchmark results, known limitations, and any semantic question encountered.

### Stop conditions

If any of the following occurs, do **not** invent a solution. Stop and report the precise issue:

- grammar ambiguity forces a choice not covered here;
- an AST design conflict would change public semantics;
- evaluation order cannot be preserved with the planned codegen structure;
- a requested behavior conflicts with existing Python call semantics;
- supporting async requires a semantic choice beyond ordinary `Await(Call)` behavior;
- tests expose an ambiguity in placeholder scope or outermost-call interpretation;
- a performance optimization would observably change evaluation/error behavior.

Mechanical decisions such as function naming, helper placement, generated-file regeneration, C formatting, stack-depth bookkeeping, or choosing the appropriate existing test module are Qwen's responsibility.

## 14. Acceptance criteria

The branch is ready for human design review only when all of these are true:

- all normative examples and edge cases behave as specified;
- evaluation order is demonstrated by tests;
- AST/unparse/locations are coherent;
- simple stages compile without runtime wrapper objects;
- star/keyword-unpack cases preserve normal Python errors;
- focused and full debug-build tests pass (or every unrelated pre-existing failure is documented with evidence);
- regen/check steps are clean;
- disassembly and benchmarks are recorded;
- no language-design decisions were silently made during implementation.

At that point, review actual use on several nontrivial Python programs before deciding whether this design is good enough to pursue, whether it should be simplified to bare `x |> callable`, or whether the experiment should be abandoned.