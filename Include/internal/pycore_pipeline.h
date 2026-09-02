// Pipeline experiment: compiler-generated pipe-topic identifiers.
//
// Each syntactic Pipeline node is given a unique, source-unspellable
// hidden identifier derived from the node's address. The symtable and
// the code generator must both use this helper so they agree on the
// name for the same node.

#ifndef Py_INTERNAL_PIPELINE_H
#define Py_INTERNAL_PIPELINE_H
#ifdef __cplusplus
extern "C" {
#endif

#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif

#include "pycore_ast.h"  // expr_ty

Py_LOCAL_INLINE(PyObject *)
_PyAST_PipelineTopicName(const expr_ty node)
{
    return PyUnicode_FromFormat(".<pipe_topic_0x%zx>", (Py_uintptr_t)node);
}

#ifdef __cplusplus
}
#endif
#endif