/*
Copyright 2015-2016 P4FPGA Project

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

#ifndef _BACKENDS_FPGA_FPGAPROGRAM_H_
#define _BACKENDS_FPGA_FPGAPROGRAM_H_

#include "ir/ir.h"
#include "frontends/p4/typeMap.h"
#include "frontends/p4/evaluator/evaluator.h"
#include "frontends/p4/fromv1.0/v1model.h"
#include "codebuilder.h"
#include "translator.h"
#include "bsvprogram.h"

namespace FPGA {

class FPGAProgram;
class FPGAParser;
class FPGAControl;

// Base class for FPGA objects
class FPGAObject {
 public:
    virtual ~FPGAObject() {}
    virtual void emit(BSVProgram & bsv) = 0;
    template<typename T> bool is() const { return to<T>() != nullptr; }
    template<typename T> const T* to() const {
        return dynamic_cast<const T*>(this); }
    template<typename T> T* to() {
        return dynamic_cast<T*>(this); }
};

class FPGAProgram : public FPGAObject {
 public:
    const IR::P4Program*      program;
    const IR::ToplevelBlock*  toplevel;
    P4::ReferenceMap*         refMap;
    const P4::TypeMap*        typeMap;
    P4V1::V1Model&            v1model;
    FPGAParser*               parser;
    FPGAControl*              control;
    ExpressionTranslator*     tr;

    // write program as bluespec source code
    void emit(BSVProgram & bsv) override;
    bool build();  // return 'true' on success

    FPGAProgram(const IR::P4Program* program, P4::ReferenceMap* refMap,
                const P4::TypeMap* typeMap, const IR::ToplevelBlock* toplevel) :
            program(program), toplevel(toplevel),
            refMap(refMap), typeMap(typeMap),
            v1model(P4V1::V1Model::instance),
            // no love for this..
            tr(new ExpressionTranslator(this)){
    }

 private:
    void emitIncludes(CodeBuilder* builder);
    void emitTypes(CodeBuilder* builder);
    void emitTables(CodeBuilder* builder);
    void emitHeaderInstances(CodeBuilder* builder);
    void emitPipeline(CodeBuilder* builder);
    void emitLicense(CodeBuilder* builder);
};

}  // namespace FPGA

#endif /* _BACKENDS_FPGA_FPGAPROGRAM_H_ */
