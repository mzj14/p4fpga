# Copyright (c) 2016 P4FPGA Project
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
#

import astbsv as ast
import config
import bsvgen_common
import logging
from sourceCodeBuilder import SourceCodeBuilder
from utils import CamelCase, camelCase, GetFieldWidth
from utils import GetHeaderInState, GetHeaderType, GetHeaderWidth
from utils import GetExpressionInState

logger = logging.getLogger(__name__)

class Parser(object):
    # proper use of parser class constant?
    def __init__(self, parse_rules, transitions, transition_key, map_merged_state, map_parse_state_reverse, states):
        #def __init__(self, states):

        self.rules = parse_rules
        self.transitions = transitions
        self.transition_key = transition_key
        self.map_merged_state = map_merged_state
        self.map_parse_state_reverse = map_parse_state_reverse

        self.mutex_rules = {}
        self.transition_rules = {}
        self.pulse_wires = set()
        self.dwires = set()
        self.regs = set()

        self.states = states

    def emitInterface(self, builder):
        logger.info("emitParser")
        TMP1 = "Put#(EtherData)"
        TMP2 = "Get#(MetadataT)"
        TMP3 = "Put#(int)"
        TMP4 = "ParserPerfRec"
        intf = ast.Interface(typedef="Parser")
        intf.subinterfaces.append(ast.Interface("frameIn", TMP1))
        intf.subinterfaces.append(ast.Interface("meta", TMP2))
        intf.subinterfaces.append(ast.Interface("verbosity", TMP3))
        intf.subinterfaces.append(ast.Method(name="read_perf_info", return_type=TMP4, params=[]))
        intf.emitInterfaceDecl(builder)

    def funct_succeed(self):
        TMP1 = "data_in_ff.deq;"
        TMP2 = "rg_offset <= offset;"
        fname = "succeed_and_next"
        rtype = "Action"
        params = "Bit#(32) offset"
        stmt = []
        stmt.append(ast.Template(TMP1))
        stmt.append(ast.Template(TMP2))
        ablock = [ast.ActionBlock(stmt)]
        funct = ast.Function(fname, rtype, params, ablock)
        return funct

    def funct_failed(self):
        TMP1 = "data_in_ff.deq;"
        TMP2 = "rg_offset <= 0;"
        fname = "failed_and_trap"
        rtype = "Action"
        params = "Bit#(32) offset"
        stmt = []
        stmt.append(ast.Template(TMP1))
        stmt.append(ast.Template(TMP2))
        ablock = [ast.ActionBlock(stmt)]
        funct = ast.Function(fname, rtype, params, ablock)
        return funct

    def funct_push_phv(self, phv):
        TMP1 = "MetadataT meta = defaultValue;"
        TMP2 = "meta.%(field)s = tagged Valid rg_tmp_%(field)s;"
        TMP3 = "meta_in_ff.enq(meta);"
        fname = "push_phv"
        rtype = "Action"
        params = "ParserState ty"
        stmt = []
        stmt.append(ast.Template(TMP1))
        for _, p in phv:
            stmt.append(ast.Template(TMP2, {"field": p}))
        stmt.append(ast.Template(TMP3))
        ablock = [ast.ActionBlock(stmt)]
        funct = ast.Function(fname, rtype, params, ablock)
        return funct

    def funct_report_parse_action(self):
        TMP1 = "$display(\"(%%d) Parser State %%h offset %%h, %%h\", $time, state, offset, data);"
        fname = "report_parse_action"
        rtype = "Action"
        params = "ParserState state, Bit#(32) offset, Bit#(128) data"
        if_stmt = ast.If("cr_verbosity[0] > 0", [])
        if_stmt.stmt.append(ast.Template(TMP1))
        stmt = []
        stmt.append(if_stmt)
        ablock = [ast.ActionBlock(stmt)]
        funct = ast.Function(fname, rtype, params, ablock)
        return funct

    def funct_compute_next_states(self, rules, transitions, transition_key):
        def compute_next_state(name, parameters, transition):
            TMP1 = "w_%(curr_state)s_%(next_state)s.send();"
            TMP2 = "nextState = %(next_state)s;"
            fname = "compute_next_state_%s" % (name)
            rtype = "Action"
            params = ', '.join(["Bit#(%s) %s" % (p[0], p[1]) for p in parameters])
            stmt = []
            a_stmt = ast.Template("let v = {%s};" % (", ".join([p[1] for p in parameters])))
            caseExpr = "v"
            case_stmt = ast.Case(caseExpr)
            ab_stmt = []
            if len(parameters) != 0:
                for t in transition:
                    value = t['value'].replace("0x", "'h")
                    next_state = t['next_state']
                    next_name = "%s" % (next_state) if next_state != None else "default"
                    action_stmt = []
                    action_stmt.append(ast.Template(TMP1 % {'curr_state': name, 'next_state': next_name}))
                    case_stmt.casePatItem[value] = value
                    case_stmt.casePatStmt[value] = action_stmt
                ab_stmt.append(a_stmt)
                ab_stmt.append(case_stmt)
            else:
                for t in transition:
                    value = t['value'].replace("0x", "'h")
                    next_state = t['next_state']
                    next_name = "%s" % (next_state) if next_state != None else "default"
                    action_stmt = []
                    action_stmt.append(ast.Template(TMP1 % {'curr_state': name, 'next_state': next_name}))
                ab_stmt += action_stmt
            stmt.append(ast.ActionBlock(ab_stmt))
            f = ast.Function(fname, rtype, params, stmt)
            return f

        funct = []
        for name, rule in rules.items():
            transition = transitions[name]
            keys = transition_key[name]
            # handle look ahead type
            params = []
            for k in keys:
                if k['type'] == 'lookahead':
                    value = k
                    width = k['value'][1] - k['value'][0]
                    params.append((width, 'current'))
                else:
                    params.append((k['width'], k['value'][1]))
            funct.append(compute_next_state(name, params, transition))
        return funct

    # need a default state to skip payload after processing header
    def rule_start(self, first_state):
        TMP1 = "let v = data_in_ff.first;"
        TMP2 = "rg_parse_state <= %(state)s;"
        TMP3 = "data_in_ff.deq;"
        stmt = []
        stmt.append(ast.Template(TMP1))
        if_stmt = []
        if_stmt.append(ast.Template(TMP2, {"state": "State{}".format(CamelCase(first_state))}))
        stmt.append(ast.If("v.sop", if_stmt))
        else_stmt = []
        else_stmt.append(ast.Template(TMP3))
        stmt.append(ast.Else(else_stmt))
        rname = "rl_start_state"
        rcond = "rg_parse_state == StateDefault"
        rule = ast.Rule(rname, rcond, stmt)
        return rule

    #FIXME: need to factor out this logic
    def find_prev_unmerged_state(self, curr_state, visited):
        if curr_state in self.map_merged_state and self.map_merged_state[curr_state] == True:
            if curr_state in visited:
                return []
            visited.add(curr_state)
            prev_states = []
            if curr_state in self.map_parse_state_reverse:
                prev_states = self.map_parse_state_reverse[curr_state]

            all_states = []
            print 'xxxxxxxxx', all_states, curr_state, prev_states
            for state in prev_states:
                all_states += self.find_prev_unmerged_state(state, visited)
            return all_states
        else:
            return [ curr_state ]

    #def rule_transition():
    # FIXME: merge each of this rule into a PARSE_RULE class
    def build_transition_rules(self, curr_state, transitions, mutex_rules):
        stmt = []
        rules = []
        visited = set()
        for transition in transitions:
            next_state = transition['next_state']
            if next_state == None:
                next_state = 'default'

            wname = "w_%s_%s" % (curr_state, next_state)
            self.pulse_wires.add(wname)

            if next_state in self.map_merged_state and self.map_merged_state[next_state] == True:
                continue

            for state in self.find_prev_unmerged_state(curr_state, visited):
                rname = "rl_%s_%s" % (state, next_state)
                rcond = "(rg_parse_state == %s) && (%s)" % ("State%s"%(CamelCase(state)), wname)
                print 'xxx', next_state
                rstmt = ast.Template("rg_parse_state <= %s;" % ("State%s"%(CamelCase(next_state))))
                stmt.append(ast.Rule(rname, rcond, [rstmt]))
                rules.append(rname)
                # add rule to mutex rule dict
                if state not in mutex_rules:
                    mutex_rules[state] = [rname]
                else:
                    mutex_rules[state].append(rname)
        return stmt

    #FIXME: to its own PARSE_RULE subclass
    #class ParseRuleBegin(ParseRule):
    # mergedRule(ruleObject):
    # rule.emit()
    def build_merged_parse_rule(self, name, pdict, next_states):
        TMP1 = "Vector#(%(rcvdlen)s, Bit#(1)) dataVec = unpack(%(w_name)s);"
        TMP2 = "let %(header_type)s = extract_%(header_type)s(pack(takeAt(%(header_offset)s, dataVec)));"
        TMP3 = "compute_next_state_%(name)s(%(field)s);"
        TMP4 = "Vector#(%(nextlen)s, Bit#(1)) unparsed = takeAt(%(width)s, dataVec);"
        TMP5 = "rg_tmp_%(next_state)s <= zeroExtend(pack(unparsed));"
        TMP6 = "succeed_and_next(%(nextlen)s);"
        prev_states = []
        if name in self.map_parse_state_reverse:
            prev_states = self.map_parse_state_reverse[name]
        rules = []
        for state in prev_states:
            stmt = []
            rname = "rl_%s" % (name)
            wname = "w_%s_%s" % (state, name)
            rcvdlen = 0#self.rules[state][-1].rcvdlen
            prevLen = rcvdlen - config.DP_WIDTH
            unparsed = 0#self.rules[state][-1].nextlen
            rcond = "(rg_parse_state == State%s) && (rg_offset == %s) && (%s)" % (CamelCase(state), prevLen, wname)
            stmt.append(ast.Template(TMP1, {"rcvdlen": rcvdlen, 'w_name': 'w_%s_data' % (name)}))
            hdrs = GetHeaderInState(name)
            hdr_offset = rcvdlen - unparsed
            hdr_len = 0
            for hdr in hdrs:
                stmt.append(ast.Template(TMP2, {'header_type': GetHeaderType(hdr), 'header_offset': hdr_offset}))
                hdr_offset += GetHeaderWidth(hdr)
                hdr_len += GetHeaderWidth(hdr)
            stmt.append(ast.Template(TMP3, pdict))
            # if next state is not merged
            for nxt in next_states:
                if self.map_merged_state[nxt] == False:
                    stmt.append(ast.Template(TMP4, {'nextlen': unparsed - hdr_len, 'width': hdr_offset}))
                    stmt.append(ast.Template(TMP5, {'next_state': nxt}))
                    stmt.append(ast.Template(TMP6, {'nextlen': unparsed - hdr_len}))
            rules.append(ast.Rule(rname, rcond, stmt))
        return rules

    # PARSE_RULE
    # class ParseRuleFinalize (ParseRule):
    def build_last_rule_for_header(self, pdict, hdrs, next_states):
        TMP1 = "Vector#(%(prevLen)s, Bit#(1)) tmp_dataVec = unpack(truncate(rg_tmp_%(name)s));"
        TMP2 = "Bit#(%(prevLen)s) data_last_cycle = pack(takeAt(0, tmp_dataVec));"
        TMP3 = "Bit#(%(rcvdlen)s) data = {data_this_cycle, data_last_cycle};"
        TMP4 = "Vector#(%(rcvdlen)s, Bit#(1)) dataVec = unpack(data);"
        TMP5 = "let %(header_type)s = extract_%(header_type)s(pack(takeAt(%(header_offset)s, dataVec)));"
        TMP6 = "compute_next_state_%(name)s(%(field)s);"
        TMP8 = "Vector#(%(nextlen)s, Bit#(1)) unparsed = takeAt(%(width)s, dataVec);"
        TMP9 = "rg_tmp_%(next_state)s <= zeroExtend(pack(unparsed));"
        TMP10 = "parse_state_w <= %(state)s;"
        TMP11 = "succeed_and_next(%(nextlen)s);"
        TMP12 = "push_phv(%(state)s);"
        TMP13 = "let v = %(expr)s;"
        TMP14 = "%(name)s <= v;"
        stmt = []
        stmt.append(ast.Template(TMP1, pdict))
        stmt.append(ast.Template(TMP2, pdict))
        stmt.append(ast.Template(TMP3, pdict))
        stmt.append(ast.Template(TMP4, pdict))

        header_offset = 0
        for hdr in hdrs:
            stmt.append(ast.Template(TMP5, {"header_type": GetHeaderType(hdr),
                                            "header_offset": header_offset}))
            header_offset += GetHeaderWidth(hdr)
        type, dst, src = GetExpressionInState(pdict['name'])
        if dst and src:
            if type == 'expression':
                # replace 0x1 with 'h1
                stmt.append(ast.Template(TMP13, {'expr' : ' '.join([x.replace('0x', "'h") for x in src])}))
            elif type == 'field':
                width = GetFieldWidth(src)
                src[0] = GetHeaderType(src[0]) #FIXME mutated data
                stmt.append(ast.Template(TMP13, {'expr' : '.'.join(src)}))
                self.regs.add((width, dst[0]))

            stmt.append(ast.Template(TMP14, {'name' : dst[0]}))
            stmt.append(ast.Template(TMP6, {'name': pdict['name'], 'field': 'v'}))
        else:
            stmt.append(ast.Template(TMP6, pdict))

        stmt.append(ast.Template(TMP8, pdict))
        b_incr_offset = False
        for s in next_states:
            if s in self.map_merged_state and self.map_merged_state[s] == True:
                stmt.append(ast.Template("w_%s_data <= data;" % (s)))
                self.dwires.add(("w_%s_data"%(s), pdict['rcvdlen']))
                continue
            stmt.append(ast.Template(TMP9, {'next_state': s, 'name': pdict['name']}))
            if b_incr_offset == False:
                stmt.append(ast.Template(TMP11, pdict))
                b_incr_offset = True
        if len(next_states) == 0:
            stmt.append(ast.Template(TMP12, pdict))
            stmt.append(ast.Template(TMP10, pdict))
        return stmt

    def build_non_last_rule_for_header(self, pdict):
        TMP1 = "Vector#(%(prevLen)s, Bit#(1)) tmp_dataVec = unpack(truncate(rg_tmp_%(name)s));"
        TMP2 = "Bit#(%(prevLen)s) data_last_cycle = pack(takeAt(0, tmp_dataVec));"
        TMP3 = "Bit#(%(rcvdlen)s) data = {data_this_cycle, data_last_cycle};"
        TMP4 = "rg_tmp_%(name)s <= zeroExtend(data);"
        TMP5 = "succeed_and_next(%(rcvdlen)s);"
        stmt = []
        stmt.append(ast.Template(TMP1, pdict))
        stmt.append(ast.Template(TMP2, pdict))
        stmt.append(ast.Template(TMP3, pdict))
        stmt.append(ast.Template(TMP4, pdict))
        stmt.append(ast.Template(TMP5, pdict))
        return stmt

    def compile_rule(self, rule, transition_key, transitions):
        TMP1 = "rl_parse_%(name)s_%(idx)s"
        TMP2 = "(rg_parse_state == %(state)s) && (rg_offset == %(prevLen)s)"
        TMP3 = "report_parse_action(rg_parse_state, rg_offset, data_this_cycle);"

        # Rule info from jsondata after expanded for multi-cycles
        # it does not consider merging rules.
        #FIXME: remove all these...
        name = rule.name
        first = rule.firstBeat
        last = rule.lastBeat
        idx = rule.idx
        rcvdlen = rule.rcvdlen
        nextlen = rule.nextlen
        offset = rule.offset
        width = rule.width
        # Transition may depend on multiple keys, represent with a list
        # select key should be set at p4fpga.py
        keys = []
        #FIXME : if key is metadata, check expression to update key
        for key in transition_key[name]:
            #print 'xxx', transition_key[name]
            if key['type'] == 'lookahead':
                print "WARNING: unhandled lookahead header" 
                continue
            keys.append("%s.%s" %(GetHeaderType(key['value'][0]), key['value'][1]))

        # Collect all next states except default transition to start state,
        # used to save temp unparsed bit in pipeline registers
        #FIXME: next_state should be set at p4fpga.py
        next_states = set()
        for transition in transitions[name]:
            next_state = transition['next_state']
            if next_state == None:
                continue
            next_states.add(next_state)

        # rule to do most of parsing work.
        # build data structure for bsvgen
        # this data structure does not consider merged state
        hdrs = GetHeaderInState(name)
        pdict = {"name": name,
                 "idx": idx,
                 "state": "State"+CamelCase(name),
                 "header_type": hdrs,
                 "offset": offset,
                 "rcvdlen": rcvdlen,
                 "prevLen": rcvdlen - config.DP_WIDTH,
                 "nextlen": nextlen,
                 "width": width,
                 "dp_width": config.DP_WIDTH,
                 "field": ",".join(keys)}

        # Variable to save all generated bluespec rules
        # determine rule type
        # if rule_type == "RULE_PROC"
        #    self.build_dequeue_rule()
        # elif rule_type == "RULE_FINAL"
        #    self.build_extract_rule()
        # elif rule_type == "RULE_TRANSIT"
        #    self.build_transit_rule()
        # elif rule_type == "RULE_MERGE"
        #    self.build_merged_extract_rule()
        # else:
        #    #error
        rules = []
        if self.map_merged_state[name] == True:
            if name in self.map_parse_state_reverse:
                last_state = self.map_parse_state_reverse[name]
                print ("%s: %s" % (name, last_state))
                print 'merge go ahead'
            # rule_type = RULE_MERGED
            rules += self.build_merged_parse_rule(name, pdict, next_states)
        else:
            rname = TMP1 % pdict
            rcond = TMP2 % pdict
            stmt = []
            stmt.append(ast.Template(TMP3, pdict))
            # distinguish between last rule for a header and rest of rules.
            # last rule needs to handle state transition
            if last:
                stmt += self.build_last_rule_for_header(pdict, hdrs, next_states)
            else:
                stmt += self.build_non_last_rule_for_header(pdict)
            # rule_type = RULE_DEQUEUE
            # rule_type = RULE_EXTRACT
            rules.append(ast.Rule(rname, rcond, stmt))
        print rule

        if last:
            temp = self.build_transition_rules(name, transitions[name], self.mutex_rules)
            if name in self.mutex_rules:
                val = self.mutex_rules[name]
                TMP4 = "(* mutually_exclusive = \"%(rule)s\" *)"
                rules.append(ast.Template(TMP4, {'rule': ','.join(val)}))
            rules += temp

        return rules

    def buildFFs(self):
        TMP1 = "FIFOF#(EtherData) data_in_ff <- mkFIFOF;"
        TMP2 = "FIFOF#(MetadataT) meta_in_ff <- mkFIFOF;"
        TMP3 = "Reg#(ParserState) rg_parse_state <- mkReg(StateDefault);"
        TMP4 = "Wire#(ParserState) parse_state_w <- mkDWire(StateDefault);"
        TMP5 = "Reg#(Bit#(32)) rg_offset <- mkReg(0);"
        TMP6 = "PulseWire parse_done <- mkPulseWire();"
        stmt = []
        stmt.append(ast.Template(TMP1))
        stmt.append(ast.Template(TMP2))
        stmt.append(ast.Template(TMP3))
        stmt.append(ast.Template(TMP4))
        stmt.append(ast.Template(TMP5))
        stmt.append(ast.Template(TMP6))
        return stmt

    def buildTmpRegs(self, phv):
        TMP1 = "Reg#(Bit#(%(sz)s)) rg_tmp_%(name)s <- mkReg(0);"
        stmt = []
        for state, parse_steps in self.rules.items():
            for rule in parse_steps:
                rcvdlen = rule.rcvdlen
                last = rule.lastBeat
                if last:
                    stmt.append(ast.Template(TMP1, {'sz': rcvdlen, 'name': state}))

        for sz, name in phv:
            stmt.append(ast.Template(TMP1, {'sz': sz, 'name': name}))

        return stmt

    def build_phv(self):
        metadata = set()
        fields = []
        for it in config.ir.basic_blocks.values():
            for f in it.request.members:
                if f not in metadata:
                    width = GetFieldWidth(f)
                    name = "$".join(f)
                    fields.append((width, name))
                    metadata.add(f)
        for f in config.ir.controls.values():
            for _, v in f.tables.items():
                for k in v.key:
                    d = tuple(k['target'])
                    if d not in metadata:
                        width = GetFieldWidth(k['target'])
                        name = "$".join(k['target'])
                        fields.append((width, name))
                        metadata.add(d)
        #for it in config.ir.parsers.values():
        #    for h in it.header_instances.values():
        #        name = "valid_%s" % (camelCase(h))
        #        fields.append((0, name))
        return fields

    def buildModuleStmt(self):
        # boiler plate
        phv = self.build_phv()
        stmt = []
        stmt += bsvgen_common.buildVerbosity()
        stmt += self.buildFFs()
        stmt += self.buildTmpRegs(phv)
        stmt.append(self.funct_succeed())
        stmt.append(self.funct_failed())
        stmt.append(self.funct_push_phv(phv))
        stmt.append(self.funct_report_parse_action())
        # FIXME
        stmt += self.funct_compute_next_states(self.rules, self.transitions, self.transition_key)
        stmt.append(ast.Template("let data_this_cycle = data_in_ff.first.data;"))

        #FIXME
        #first_state = self.rules.keys()[0]
        #stmt.append(self.rule_start(first_state))

        # stmt += self.generate_functions()
        for _, state in self.states.items():
            for rule in state.rules:
                rule.name = state.name #FIXME
                #FIXME
                self.compile_rule(rule, self.transition_key, self.transitions)

        #HACK
        # fill in missing registers
        for reg in self.regs:
            stmt.insert(0, ast.Template("Reg#(Bit#(%s)) %s <- mkReg(0);"% (reg[0], reg[1])))
        for wire in self.pulse_wires:
            stmt.insert(0, ast.Template("PulseWire %(name)s <- mkPulseWireOR();", {'name': wire}))
        for wire in self.dwires:
            stmt.insert(0, ast.Template("Wire#(Bit#(%(width)s)) %(name)s <- mkDWire(0);", {'name': wire[0], 'width': wire[1]}))
        stmt.append(ast.Template("interface frameIn = toPut(data_in_ff);"))
        stmt.append(ast.Template("interface meta = toGet(meta_in_ff);"))
        stmt.append(ast.Template("interface verbosity = toPut(cr_verbosity_ff);"))
        return stmt

    def emitTypes(self, builder):
        elem = []
        elem.append(ast.EnumElement("StateDefault", None, None))
        for state in self.rules.keys():
            elem.append(ast.EnumElement("State%s" % (CamelCase(state)), None, None))
        state = ast.Enum("ParserState", elem)
        state.emit(builder)

    def emitModule(self, builder):
        logger.info("emitModule: Parser")
        mname = "mkParser"
        iname = "Parser"
        params = []
        provisos = []
        decls = []
        stmt = self.buildModuleStmt()
        module = ast.Module(mname, params, iname, provisos, decls, stmt)
        module.emit(builder)

    def emit(self, builder):
        builder.newline()
        builder.append("// ====== PARSER ======")
        builder.newline()
        builder.newline()
        assert isinstance(builder, SourceCodeBuilder)
        self.emitTypes(builder)
        self.emitInterface(builder)
        self.emitModule(builder)
