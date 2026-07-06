import json
import os
from pathlib import Path
from typing import Optional, List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_project_path(path: str | Path) -> Path:
    """Resolve a relative path from cwd or the project root."""
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    for candidate in (Path.cwd() / p, PROJECT_ROOT / p):
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_ROOT / p).resolve()

class Condition:
    def __init__(self, cond_type: str, left=None, right=None,
                 operand=None, operands=None):
        self.type = cond_type
        self.left = left
        self.right = right
        self.operand = operand        # for unary
        self.operands = operands      # for n-ary

    def to_dict(self):
        d = {"type": self.type}

        # Leaf binary conditions stay the same
        if self.type in ("equal", "not_equal",
                         "greater", "less", "greater_equal", "less_equal"):
            d["left"] = self.left
            d["right"] = self.right
            return d

        # Unary NOT → convert to operands list
        if self.type == "not":
            d["operands"] = [self.operand.to_dict()]
            return d

        # AND / OR → stays operands
        if self.type in ("and", "or"):
            d["operands"] = [op.to_dict() for op in self.operands]
            return d

        raise ValueError(f"Unknown condition type: {self.type}")


class Branch:
    def __init__(self, target: str, condition: Optional[Condition] = None):
        self.target = target
        self.condition = condition

    def to_dict(self):
        return {
            "condition": self.condition.to_dict() if self.condition else None,
            "target": self.target
        }


class Node:
    def __init__(self, node_id: str, instructions: List[str], branches: List[Branch]):
        self.node_id = node_id
        self.instructions = instructions
        self.branches = branches

    def to_dict(self):
        return {
            "instructions": self.instructions,
            "branches": [b.to_dict() for b in self.branches]
        }


class Protocol:
    def __init__(self, start_node: str):
        self.start_node = start_node
        self.nodes: Dict[str, Node] = {}

    def add_node(self, node: Node):
        self.nodes[node.node_id] = node

    def to_dict(self):
        return {
            "start_node": self.start_node,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()}
        }

    def save_to_file(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=4)
        print(f"Protocol saved to {filepath}")



import json
from typing import Dict, List, Optional

# assumes your existing classes:
# class Condition, class Branch, class Node

def condition_from_dict(d: Optional[dict]) -> Optional[Condition]:
    """Rebuild a Condition (possibly nested) from a JSON dict."""
    if d is None:
        return None

    ctype = d["type"]

    # Leaf comparisons
    if ctype in ("equal", "not_equal", "greater", "less", "greater_equal", "less_equal"):
        return Condition(
            cond_type=ctype,
            left=d.get("left"),
            right=d.get("right")
        )

    # NOT: in your JSON, it's encoded as { "type": "not", "operands": [ subcond ] }
    if ctype == "not":
        ops = d.get("operands", [])
        if len(ops) != 1:
            raise ValueError("NOT condition expects exactly one operand")
        sub = condition_from_dict(ops[0])
        return Condition(cond_type="not", operand=sub)

    # AND / OR: { "type": "and", "operands": [ ... ] }
    if ctype in ("and", "or"):
        ops = d.get("operands", [])
        subconds = [condition_from_dict(x) for x in ops]
        return Condition(cond_type=ctype, operands=subconds)

    raise ValueError(f"Unknown condition type in JSON: {ctype}")

# ---- parse Branch from dict ----

def branch_from_dict(d: dict) -> Branch:
    cond_dict = d.get("condition")
    cond = condition_from_dict(cond_dict) if cond_dict is not None else None
    return Branch(
        target=d["target"],
        condition=cond
    )

def node_from_dict(node_id: str, d: dict) -> Node:
    instrs = d.get("instructions", [])
    branches_dicts = d.get("branches", [])
    branches = [branch_from_dict(bd) for bd in branches_dicts]
    return Node(node_id=node_id, instructions=instrs, branches=branches)



#-----visualization and path reading -----
import json
from graphviz import Digraph

def visualize_protocol_from_file(filepath: str, outname="protocol_graph"):
    # Load JSON
    with open(_resolve_project_path(filepath), "r") as f:
        data = json.load(f)

    dot = Digraph(comment="Protocol Graph")

    nodes = data["nodes"]

    # Add nodes
    for node_id, node_data in nodes.items():
        label = f"{node_id}\n" + "\n".join(node_data["instructions"])
        dot.node(node_id, label)

        # Add branches
        for br in node_data["branches"]:
            target = br["target"]
            cond = br["condition"]

            if cond is None:
                cond_label = "unconditional"
            else:
                cond_label = json.dumps(cond, indent=2)

            dot.edge(node_id, target, label=cond_label)

    # Render to SVG
    dot.render(outname, format="svg", cleanup=True)
    print(f"Saved visualization to {outname}.svg")


from typing import Tuple

def read_path_steps(nodes: dict, path: list):
    steps = []
    for i, nid in enumerate(path):
        if nid not in nodes:
            raise KeyError(f"Node '{nid}' not found in protocol")

        node = nodes[nid]
        cond_to_next = None

        if i + 1 < len(path):
            next_id = path[i+1]
            # find branch leading to next_id
            for br in node.branches:
                if br.target == next_id:
                    cond_to_next = br.condition.to_dict() if br.condition else None
                    break

        steps.append((nid, node.instructions, cond_to_next))
    return steps


def classify_terminal_path(instruction: Optional[str]) -> int:
    """
    Classify a terminal path from its final instruction.

        Rules:
            Type 0 -> Break (or missing)
                        Type 1 -> terminal instruction contains "raw"
                        Type 2 -> anything else
    """
    normalized = (instruction or "").strip().lower()
    if normalized in ("", "break"):
        return 0
    if "raw" in normalized:
        return 1
    return 2
# ---- top-level loader ----

def load_protocol(path: str):
    """
    Load a protocol JSON of the form:
    {
        "start_node": "<id>",
        "nodes": {
            "<id>": { "instructions": [...], "branches": [...] },
            ...
        }
    }

    Returns:
      start_node: str
      protocol: Dict[str, Node]
    """
    with open(_resolve_project_path(path), "r") as f:
        raw = json.load(f)

    start_node = raw["start_node"]
    nodes_dict = raw["nodes"]   # dict: node_id -> dict

    protocol: Dict[str, Node] = {}
    for node_id, node_body in nodes_dict.items():
        protocol[node_id] = node_from_dict(node_id, node_body)

    return start_node, protocol
def all_paths_with_conditions_and_instructions(protocol):
    """
    Yields:
      (branch_steps, condition_list, instruction_list)

    where:
      branch_steps      = [(from_node_id, Branch, to_node_id), ...]
      condition_list    = [Condition, Condition, ...]    # one per branch that has a condition
      instruction_list  = [str, str, ...]                # instructions along the path

    - If a branch has no condition, it does NOT add to condition_list.
    - If a path has no conditions at all, condition_list will be [].
    """

    def dfs(node_id, branch_steps, cond_list, instr_list):
        node = protocol.nodes[node_id]

        # Add this node's instructions
        new_instr_list = instr_list + node.instructions

        # Leaf node → complete path
        if not node.branches:
            yield branch_steps, cond_list, new_instr_list
            return

        for br in node.branches:
            # Record this transition: from node_id to br.target
            step = (node_id, br, br.target)

            # Add this branch's condition (if any)
            if br.condition is None:
                new_cond_list = cond_list.copy()
            else:
                new_cond_list = cond_list + [br.condition]

            if br.target is None:
                # Terminal branch (no next state)
                yield branch_steps + [step], new_cond_list, new_instr_list
            else:
                # Continue DFS from the target state
                yield from dfs(
                    br.target,
                    branch_steps + [step],
                    new_cond_list,
                    new_instr_list
                )

    yield from dfs(protocol.start_node, [], [], [])

def build_protocol_d_3_lai() -> Protocol:
    protocol = Protocol(start_node="root")

    condition_s_0_all_zero = Condition(cond_type="equal", left="s_0", right= False)
    condition_f_0_all_zero = Condition(cond_type="equal", left="f_0", right= False)
    condition_0 = Condition("and", operands=[condition_s_0_all_zero, condition_f_0_all_zero])
    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome"],
        branches=[Branch(target="f_0_s_0_all_zero", condition= condition_0),
                  Branch(target="not_f_0_s_0_all_zero", condition = Condition("not", operand=condition_0))]
    )
    protocol.add_node(root_node)

    f_0_s_0_all_zero = Node(
        node_id="f_0_s_0_all_zero",
        instructions=["Break"], branches=[]
    )
    protocol.add_node(f_0_s_0_all_zero)

    #node f_1_s_1_all_zero
    not_f_0_s_0_all_zero = Node(
        node_id="not_f_0_s_0_all_zero",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_1")]
    )
    protocol.add_node(not_f_0_s_0_all_zero)

    # Add the missing ter_1 node
    ter_1_node = Node(
        node_id="ter_1",
        instructions=["LUT_s_0_f_0_s_1"],
        branches=[]
    )
    protocol.add_node(ter_1_node)

    # Flag measurement node
    flag_measure_node = Node(
        node_id="ter_2",
        instructions=[],
        branches=[]
    )
    protocol.add_node(flag_measure_node)

    protocol.save_to_file("./protocols/d_3_lai_protocol.json")

    return protocol
def build_protocol_d_5_lai() -> Protocol:
    protocol = Protocol(start_node="root")



    #condition s_0 == 0 
    condition_s_0_all_zero = Condition( cond_type="equal",left="s_0", right=False)
    #condition flag are all zero
    condition_f_0_all_zero = Condition(cond_type="equal", left="f_0", right= False)
    
    condition_0 = Condition("and", operands=[condition_s_0_all_zero, condition_f_0_all_zero])
    

    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome"],
        branches=[Branch(target="s_0_f_0_all_zero", condition= condition_0),
                  Branch(target="s_0_f_0_not_all_zero", condition = Condition("not", operand=condition_0))]
    )

    protocol.add_node(root_node)


    #node all zero in first round
    round_0_all_zero = Node("s_0_f_0_all_zero",["Break"], [])

    protocol.add_node(round_0_all_zero)
    

    condition_1 = Condition("equal", left="f_1", right= False)

    s_0_f_0_not_all_zero = Node(
        node_id="s_0_f_0_not_all_zero",
        instructions=["flag_syndrome"],
        branches= [Branch(target="f_1_all_zero", condition= condition_1),
                   Branch(target="f_1_not_all_zero", condition = Condition("not", operand=condition_1))]
    )
    protocol.add_node(s_0_f_0_not_all_zero)


    
    

     #node f_1_not_all_zero
    
    condition_f_2_all_zero = Condition(cond_type="equal", left="f_2", right= False)
    condition_s_1_s_2_equal = Condition(cond_type="equal", left="s_1", right="s_2")
    condition_f_2_all_zero_and_s_1_s_2_equal = Condition("and", operands=[condition_f_2_all_zero, condition_s_1_s_2_equal])
    
    condition_not_f_2_all_zero_and_s_1_s_2_equal = Condition("not", operand= condition_f_2_all_zero_and_s_1_s_2_equal)
    

    f_1_not_all_zero = Node(
        node_id="f_1_not_all_zero",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_1")])
    protocol.add_node(f_1_not_all_zero)
    
    f_1_all_zero = Node(
        node_id="f_1_all_zero",
        instructions=["flag_syndrome"],
        branches=[Branch(target="f_2_all_zero_and_s_1_s_2_equal", condition= condition_f_2_all_zero_and_s_1_s_2_equal),
                  Branch(target="not_f_2_all_zero_s_1_s_2_equal", condition= condition_not_f_2_all_zero_and_s_1_s_2_equal)]
    )
    protocol.add_node(f_1_all_zero)    



    node = Node(node_id="ter_1", instructions=["LUT_s_0_f_0_s_1_f_1_s_2"], branches=[])
    protocol.add_node(node)
   
    node = Node("f_2_all_zero_and_s_1_s_2_equal", [],[Branch(target="ter_2")])
    protocol.add_node(node)

    node = Node("ter_2", ["LUT_s_0_f_0_s_1_f_1_s_2_f_2"],[])
    protocol.add_node(node)

    node = Node("not_f_2_all_zero_s_1_s_2_equal", ["raw_syndrome"],[Branch(target="ter_3")])
    protocol.add_node(node)
    node = Node("ter_3", ["LUT_s_0_f_0_s_1_f_1_s_2_f_2_s_3"],[])
    protocol.add_node(node)
    protocol.save_to_file("./protocols/d_5_lai_protocol.json")

    node = Node
    


    return protocol


def build_protocol_d_5_mine() -> Protocol:
    protocol = Protocol(start_node="root")



    #condition s_0 == 0 
    condition_s_0_all_zero = Condition( cond_type="equal",left="s_0", right=False)
    #condition flag are all zero
    condition_f_0_all_zero = Condition(cond_type="equal", left="f_0", right= False)
    
    condition_0 = Condition("and", operands=[condition_s_0_all_zero, condition_f_0_all_zero])
    

    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome_Z"],
        branches=[Branch(target="s_0_f_0_all_zero", condition= condition_0),
                  Branch(target="s_0_f_0_not_all_zero", condition = Condition("not", operand=condition_0))]
    )

    protocol.add_node(root_node)


    #node all zero in first round
    round_0_all_zero = Node("s_0_f_0_all_zero",["Break"], [])

    protocol.add_node(round_0_all_zero)
    

    condition_1 = Condition("equal", left="f_1", right= False)

    s_0_f_0_not_all_zero = Node(
        node_id="s_0_f_0_not_all_zero",
        instructions=["flag_syndrome"],
        branches= [Branch(target="f_1_all_zero", condition= condition_1),
                   Branch(target="f_1_not_all_zero", condition = Condition("not", operand=condition_1))]
    )
    protocol.add_node(s_0_f_0_not_all_zero)


    
    

     #node f_1_not_all_zero
    condition_s_1_all_zero = Condition(cond_type="equal", left="s_1", right= False)
    condition_f_2_all_zero = Condition(cond_type="equal", left="f_2", right= False)
    condition_s_1_s_2_equal = Condition(cond_type="equal", left="s_1", right="s_2")
    condition_f_2_all_zero_and_s_1_s_2_equal = Condition("and", operands=[condition_f_2_all_zero, condition_s_1_s_2_equal])
    condition_not_f_2_all_zero_and_s_1_s_2_equal = Condition("not", operand= condition_f_2_all_zero_and_s_1_s_2_equal)
    

    f_1_not_all_zero = Node(
        node_id="f_1_not_all_zero",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_1")])
    protocol.add_node(f_1_not_all_zero)
    
    f_1_all_zero = Node(
        node_id="f_1_all_zero",
        instructions=["flag_syndrome"],
        branches=[Branch(target="f_2_all_zero_and_s_1_s_2_equal", condition=  condition_f_2_all_zero_and_s_1_s_2_equal
),
                  Branch(target="not_f_2_all_zero_s_1_s_2_equal", condition= condition_not_f_2_all_zero_and_s_1_s_2_equal )]
    )
    protocol.add_node(f_1_all_zero)    



    node = Node(node_id="ter_1", instructions=["LUT_s_0_f_0_s_1_f_1_s_2"], branches=[])
    protocol.add_node(node)
   
    node = Node("f_2_all_zero_and_s_1_s_2_equal", ["raw_syndrome"],[Branch(target="ter_2")])
    protocol.add_node(node)

    node = Node("ter_2", ["LUT_s_0_f_0_s_1_f_1_s_2_f_2_s_3"],[])
    protocol.add_node(node)

    node = Node("not_f_2_all_zero_s_1_s_2_equal", ["raw_syndrome"],[Branch(target="ter_3")])
    protocol.add_node(node)
    node = Node("ter_3", ["LUT_s_0_f_0_s_1_f_1_s_2_f_2_s_3"],[])
    protocol.add_node(node)
    protocol.save_to_file("./protocols/d_5_mine_protocol.json")

    node = Node
    


    return protocol

def build_flag_protocol_chris_d_3():
    

    protocol = Protocol(start_node="root")

    #the first round has not  flag
    condition_f_0_all_zero = Condition(
    cond_type="equal",
    left="f_0",
    right=False)

    #the first round has a flag
    condition_r_1_flagged = Condition(cond_type = "not", operand= condition_f_0_all_zero)


    #root node
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome"],
        branches=[Branch(target="r_0_flagged", condition = condition_r_1_flagged),
                  Branch(target="r_0_not_flagged", condition = condition_f_0_all_zero)]

    )
    protocol.add_node(root_node)

    #the first round has a flag 
    r_0_flagged = Node(
        node_id="r_0_flagged",
        instructions=["raw_syndrome"], branches=[Branch(target="ter_1")])
    
    protocol.add_node(r_0_flagged)

    ter_1 = Node("ter_1", ["LUT_s_0_f_0"], [])

    protocol.add_node(ter_1)

    #the second round has not flag
    condition_f_1_all_zero = Condition(cond_type="equal", left="f_1", right=False)

    #the second round has a flag
    condition_f_1_flagged = Condition(cond_type = "not", operand= condition_f_1_all_zero)   

    #the synderome of second round is the same as the first round
    condition_s_1_eq_s0 = Condition(cond_type="equal", left="s_1", right="s_0")
    condition_s_1_neq_s0 = Condition(cond_type = "not", operand= condition_s_1_eq_s0)

    r_0_not_flagged  = Node(
        node_id="r_0_not_flagged",
        instructions=["flag_syndrome"],
        branches=[Branch(target="r_1_flagged", condition = condition_f_1_flagged),
                  Branch(target="r_1_not_flagged_and_s1_neq_s0", condition = Condition("and", operands=[condition_f_1_all_zero, condition_s_1_neq_s0])),
                    Branch(target="r_1_not_flagged_and_s1_eq_s0", condition = Condition("and", operands=[condition_f_1_all_zero, condition_s_1_eq_s0]))]
    )
    protocol.add_node(r_0_not_flagged)

    #second round is flagged    
    r_1_flagged = Node(
        node_id="r_1_flagged",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_2")]
    )
    protocol.add_node(r_1_flagged)
    ter_2 = Node("ter_2", ["LUT_s_0_f_0_s_1_f_1"], [])
    protocol.add_node(ter_2)
    #second round is not flagged and s_2 != s_1
    r_1_not_flagged_s1_neq_s0 = Node(
        node_id="r_1_not_flagged_and_s1_neq_s0",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_3")]
    )
    protocol.add_node(r_1_not_flagged_s1_neq_s0)
    ter_3 = Node("ter_3", ["LUT_s_0_f_0_s_1_f_1"], [])
    protocol.add_node(ter_3)

    #second round is not flagged and s_2 == s_1
    r_1_not_flagged_s1_eq_s0 = Node(
        node_id="r_1_not_flagged_and_s1_eq_s0",
        instructions=["raw_syndrome"],
        branches=[Branch(target="ter_4")]
    )
    protocol.add_node(r_1_not_flagged_s1_eq_s0)
    ter_4 = Node("ter_4", ["LUT_s_0_f_0_s_1_f_1"], [])
    protocol.add_node(ter_4)    

    # Add nodes and branches as per the flag protocol structure
    # This is a placeholder for the actual implementation

    protocol.save_to_file("./protocols/chris_d_3_flag_protocol.json")
    return protocol

def build_origin_5_1_3_protocol() -> Protocol:
    protocol = Protocol(start_node="root")


    stab_gen = ["XZZXI", "IXZZX", "XIXZZ", "ZXIXZ"]
    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=[],
        branches=[]
    )

    current_node = root_node
    for i in range(len(stab_gen)):
        stab = stab_gen[i]
        condition_s_i_zero = Condition(cond_type = "equal", left=f"s_{i}", right=False)
        condition_f_i_zero = Condition(cond_type = "equal", left=f"f_{i}", right=False)
        condition_i_all_zero = Condition("and", operands=[condition_s_i_zero, condition_f_i_zero])
        current_node.instructions.append(f"{stab_gen[i]}_flag")
        current_node.branches = [
            Branch(target=f"{stab}_s_f_all_zero", condition= condition_i_all_zero),
            Branch(target=f"{stab}_s_f_not_all_zero", condition = Condition("not", operand=condition_i_all_zero))
        ]
        protocol.add_node(current_node)
        
        node =  Node(
                        node_id= f"{stab}_s_f_not_all_zero",
                        instructions=["raw_syndrome"],
                        branches=[Branch(target=f"ter_{i+1}")]
                    )
        protocol.add_node(node)


        lut_name = "LUT"
        for j in range(0,i+1):
            lut_name += f"_s_{j}_f_{j}"
            lut_name += f"_s_{i+1}"

        node =  Node(
                        node_id= f"ter_{i+1}",
                        instructions=[lut_name],
                        branches=[]
                    )
        protocol.add_node(node)


        current_node = Node(
            node_id=f"{stab}_s_f_all_zero",
            instructions=[],
            branches=[]
        )

        if i == len(stab_gen) -1:
            print("last round")
            current_node.branches = [Branch(target = f"ter_{i+2}", condition = None)]
            current_node.instructions = []
            protocol.add_node(current_node)

           

            ter_final = Node(node_id=f"ter_{i+2}", instructions=["Break"], branches=[])
            protocol.add_node(ter_final)

   
   
    protocol.save_to_file("./protocols/origin_5_1_3_protocol.json")

    return protocol


def build_low_depth_7_1_3_w_6_protocol():
    protocol = Protocol(start_node="root")
    stab_gen = ["IZZXXYY", "XIXYZYZ", "ZXYYXZI"]
    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=[],
        branches=[]
    )

    condition_s_i_one = Condition(cond_type = "equal", left=f"s_0", right=True)
    condition_f_i_zero = Condition(cond_type = "equal", left=f"f_0", right= False)

    condition_s_one_f_zero = Condition("and", operands=[condition_s_i_one, condition_f_i_zero])


    root_node.instructions.append(f"{stab_gen[0]}")
    root_node.branches = [
        Branch(target=f"{stab_gen[0]}_s_one_f_zero", condition=condition_s_one_f_zero),
        Branch(target=f"{stab_gen[0]}_not_f_zero", condition=Condition("not", operand=condition_s_one_f_zero)),
    ]
    protocol.add_node(root_node)

    node =  Node(
                        node_id= f"{stab_gen[0]}_not_f_zero",
                        instructions=["raw_syndrome"],
                        branches=[Branch(target=f"ter_1")]
                    )
    protocol.add_node(node)
    ter_1 = Node(
        node_id="ter_1",
        instructions=["LUT_s_0_f_0_s_1"],
        branches=[]
    )
    protocol.add_node(ter_1)



    current_node = Node(f'{stab_gen[0]}_s_one_f_zero', [], [])




    for i in range(1,len(stab_gen)):
        stab = stab_gen[i]
        condition_s_i_one = Condition(cond_type = "equal", left=f"s_{i}", right=True)
        condition_f_i_zero = Condition(cond_type = "equal", left=f"f_{i}", right= False)
        condition_not_s_one_f_zero = Condition("and", operands=[condition_s_i_one, condition_f_i_zero])
        current_node.instructions.append(f"{stab_gen[i]}")
        current_node.branches = [
            Branch(target=f"{stab}_s_one_f_zero", condition= Condition("and", operands=[condition_s_i_one, condition_f_i_zero])),
            Branch(target=f"{stab}_not_s_one_f_zero", condition = Condition("not", operand= condition_not_s_one_f_zero)),
            
            
        ]
        protocol.add_node(current_node)
        
        node =  Node(
                        node_id= f"{stab}_not_s_one_f_zero",
                        instructions=["raw_syndrome"],
                        branches=[Branch(target=f"ter_{i+1}")]
                    )
        protocol.add_node(node)


        lut_name = "LUT"
        for j in range(0,i+1):
            lut_name += f"_s_{j}_f_{j}"
        lut_name += f"_s_{i+1}"

        node =  Node(
                        node_id= f"ter_{i+1}",
                        instructions=[lut_name],
                        branches=[]
                    )
        protocol.add_node(node)


        current_node = Node(
            node_id=f"{stab}_s_one_f_zero",
            instructions=[],
            branches=[]
        )

        if i == len(stab_gen) -1:
            print("last round")
            current_node.branches = [Branch(target = f"ter_{i+2}", condition = None)]
            current_node.instructions = []
            protocol.add_node(current_node)

           

            ter_final = Node(node_id=f"ter_{i+2}", instructions=["Break"], branches=[])
            protocol.add_node(ter_final)

   
   
    protocol.save_to_file("./protocols/low_depth_7_1_3_w_6_protocol.json")



def build_low_depth_7_1_3_w_6_second_protocol():
    protocol = Protocol(start_node="root")
    stab_gen = ["IIIXXXX", "IXXIIXX", "XIXIXIX", "IIIIZZZZ", "IZZIIZZZ", "ZIZIZIZI"]
    half = len(stab_gen) // 2

    def paired_raw_stab(i: int) -> str:
        # First half = X-type flag rounds; second half = Z-type. Pair with opposite-type raw.
        return stab_gen[i + half] if i < half else stab_gen[i - half]

    root_node = Node(node_id="root", instructions=[], branches=[])
    current_node = root_node

    for i in range(len(stab_gen)):
        lut_name = "LUT"
        for j in range(0, i + 1):
            lut_name += f"_s_{j}_f_{j}"

        stab = stab_gen[i]
        condition_s_i_one = Condition(cond_type="equal", left=f"s_{i}", right=True)
        condition_s_i_zero = Condition(cond_type="equal", left=f"s_{i}", right=False)
        condition_f_i_zero = Condition(cond_type="equal", left=f"f_{i}", right=False)
        condition_s_one_f_zero = Condition(
            "and", operands=[condition_s_i_one, condition_f_i_zero]
        )

        current_node.instructions.append(f"{stab}_flag")
        current_node.branches = [
            Branch(target=f"{stab}_path_s_one_f_zero", condition=condition_s_one_f_zero),
            Branch(
                target=f"{stab}_path_not_f_zero",
                condition=Condition("not", operand=condition_f_i_zero),
            ),
            Branch(
                target=f"{stab}_path_all_zero",
                condition=Condition("and", operands=[condition_s_i_zero, condition_f_i_zero]),
            ),
        ]
        protocol.add_node(current_node)

        # --- path: syndrome one, no flag ---
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_s_one_f_zero",
                instructions=["raw_syndrome_x"] if i < half else ["raw_syndrome_z"],
                branches=[Branch(target=f"{stab}_path_s_one_after_raw_x")],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_s_one_after_raw_x",
                instructions=[f"{paired_raw_stab(i)}_raw"],
                branches=[Branch(target=f"{stab}_path_s_one_ter")],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_s_one_ter",
                instructions=[lut_name + f"_s_{i + 1}" + f"_s_{i + 2}"],
                branches=[],
            )
        )

        # --- path: flag raised ---
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_zero",
                instructions=[f"{stab}_raw"],
                branches=[Branch(target=f"{stab}_path_not_f_after_raw")],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_after_raw",
                instructions=[f"{paired_raw_stab(i)}_raw"],
                branches=[
                    Branch(
                        target=f"{stab}_path_not_f_s{i + 1}_false",
                        condition=Condition(cond_type="equal", left=f"s_{i + 1}", right=False),
                    ),
                    Branch(
                        target=f"{stab}_path_not_f_s{i + 1}_true",
                        condition=Condition(cond_type="equal", left=f"s_{i + 1}", right=True),
                    ),
                ],
            )
        )
        if i == 0 or  i == 1 :
            stab_1 = stab_gen[5]
            stab_2 = stab_gen[2]
        
        elif i ==2 :
            stab_1 = stab_gen[4]
            stab_2 = stab_gen[1]
        elif i == 3 or i == 4:
            stab_1 = stab_gen[2]
            stab_2 = stab_gen[5]
        
        elif i == 5:
            stab_1 = stab_gen[1]
            stab_2 = stab_gen[4]
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_s{i + 1}_false",
                instructions=[f"{stab_1}_raw"],
                branches=[Branch(target=f"{stab}_path_not_f_s{i + 1}_false_ter")],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_s{i + 1}_false_ter",
                instructions=[lut_name + f"_s_{i + 1}" + f"_s_{i + 2}" + f"_s_{i + 3}"],
                branches=[],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_s{i + 1}_true",
                instructions=[f"{stab_2}_raw"],
                branches=[Branch(target=f"{stab}_path_not_f_s{i + 1}_true_ter")],
            )
        )
        protocol.add_node(
            Node(
                node_id=f"{stab}_path_not_f_s{i + 1}_true_ter",
                instructions=[lut_name + f"_s_{i + 1}" + f"_s_{i + 2}" + f"_s_{i + 3}"],
                branches=[],
            )
        )

        # --- path: all zero (chains to next round on clean branch) ---
        if i == len(stab_gen) - 1:
            protocol.add_node(
                Node(
                    node_id=f"{stab}_path_all_zero",
                    instructions=[],
                    branches=[Branch(target=f"{stab}_path_all_zero_break")],
                )
            )
            protocol.add_node(
                Node(
                    node_id=f"{stab}_path_all_zero_break",
                    instructions=["Break"],
                    branches=[],
                )
            )
            current_node = None
        else:
            current_node = Node(
                node_id=f"{stab}_path_all_zero",
                instructions=[],
                branches=[],
            )

    protocol.save_to_file("./protocols/low_depth_7_1_3_w_6_second_protocol.json")


def build_flag_protocol_chris_d_5():

    protocol = Protocol(start_node="root")

    condition_f_0_equal_2 = Condition(cond_type="equal", left="f_0", right=2)
    condition_f_0_equal_1 = Condition(cond_type="equal", left="f_0", right=1)
    condtion_f_0_all_zero = Condition(cond_type="equal", left="f_0", right=False)
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome"],
        branches=[Branch(target="f_0_equal_2", condition= condition_f_0_equal_2),
                  Branch(target="f_0_equal_1", condition = condition_f_0_equal_1)
                  ]
    )

    protocol.add_node(root_node)
    f_0_equal_2 = Node(
        node_id="f_0_equal_2",
        instructions=["raw_syndrome"], branches=[Branch(target="ter_1")])
    protocol.add_node(f_0_equal_2)

    ter_1 = Node(
        node_id="ter_1",
        instructions=["LUT_s_0_f_0_s_1"], branches=[])
    protocol.add_node(ter_1)


    condition_f_1_equal_1 = Condition(cond_type="equal", left="f_1", right=1)
    condtion_f_1_all_zero = Condition(cond_type="equal", left="f_1", right=False)

    condition_s_0_s_1_equal = Condition(cond_type="equal", left="s_0", right="s_1")
    condition_f_1_all_zero_and_s_0_s_1_equal = Condition("and", operands=[condtion_f_1_all_zero, condition_s_0_s_1_equal])
    condition_f_1_all_zero_and_not_s_0_s_1_equal = Condition("and", operands=[condtion_f_1_all_zero, Condition("not", operand= condition_s_0_s_1_equal)])

    f_0_equal_1= Node(
        node_id="f_0_equal_1",
        instructions=["flag_syndrome"], branches=[Branch(target="f_1_equal_1 " , condition= condition_f_1_equal_1),
                                                  Branch(target="f_1_all_zero_s_0_eq_s_1", condition= condition_f_1_all_zero_and_s_0_s_1_equal),
                                                Branch(target="f_1_all_zero_s_0_neq_s_1", condition = condition_f_1_all_zero_and_not_s_0_s_1_equal)
                                                  ])
    
    protocol.add_node(f_0_equal_1)

    f_1_equal_1 = Node(
        node_id="f_1_equal_1 ",
        instructions=["raw_syndrome"], branches=[Branch(target="ter_2")])
    protocol.add_node(f_1_equal_1)

    ter_2 = Node(
        node_id="ter_2",
        instructions=["LUT_s_0_f_0_s_1_f_1_s_2"], branches=[])
    protocol.add_node(ter_2)

    f_1_all_zero_s_0_neq_s_1 = Node(
        node_id="f_1_all_zero_s_0_neq_s_1",
        instructions=["raw_syndrome"], branches=[Branch(target="ter_3")])
    protocol.add_node(f_1_all_zero_s_0_neq_s_1)

    ter_3 = Node(
        node_id="ter_3",
        instructions=["LUT_s_0_f_0_s_1_f_1_s_2"], branches=[])
    
    protocol.add_node(ter_3)

    f_1_all_zero_s_0_eq_s_1 = Node(
        node_id="f_1_all_zero_s_0_eq_s_1",
        instructions=[], branches=[Branch(target="ter_4")])
    protocol.add_node(f_1_all_zero_s_0_eq_s_1)

    ter_4 = Node(
        node_id="ter_4",
        instructions=["LUT_s_0_f_0_s_1_f_1"], branches=[])
    protocol.add_node(ter_4)
    
    
    protocol.save_to_file("./protocols/chris_d_5_falg_protocol.json")



def build_demo_prtocol():
    protocol = Protocol(start_node="root")

    # Root node with unconditional branch to flag_measure
    root_node = Node(
        node_id="root",
        instructions=["flag_syndrome"],
        branches=[Branch(target="ter_1" ,condition = Condition("equal", left="f_0", right= True)),
                  Branch(target="ter_2", condition = Condition("not", operand= Condition("equal", left="f_0", right= True)))]
    )
    protocol.add_node(root_node)

    ter_1 = Node(
        node_id="ter_1",
        instructions=["LUT_s_0_f_0"],
        branches=[]
    )
    protocol.add_node(ter_1)

    ter_2 = Node(
        node_id="ter_2",
        instructions=["LUT_s_0"],
        branches=[]
    )
    protocol.add_node(ter_2)
    protocol.save_to_file("./protocols/demo_protocol.json")


    return protocol


def build_5_1_3_low_depth_protocol():

    protocol = Protocol(start_node="root")

    gen = ["XZZXI", "IXZZX", "XIXZZ", "ZXIXZ"]
    gen_2= ["YXXYI", "IYXXY", "YIYXX", "YIYXX" , "YZIZY"]
    gen_3 = [["ZIZYY", "XIXZZ"], ["YZIZY", "ZXIXZ"], ["YZIZY","ZXIXZ"], ["YIYXX", "XIXZZ"]]



    root_node = Node(
        node_id=f"root",
        instructions=[],
        branches=[]
    )
    
    current_node = root_node

    for stab in gen:
        condition_s_i_zero = Condition(cond_type = "equal", left=f"s_{gen.index(stab)}", right=False)
        condition_f_i_zero = Condition(cond_type = "equal", left=f"f_{gen.index(stab)}", right=False)
        condition_i_all_zero = Condition("and", operands=[condition_s_i_zero, condition_f_i_zero])
        condition_and_s_i_not_zero_f_zero = Condition("and", operands=[Condition("not", operand= condition_s_i_zero), condition_f_i_zero])
        condition_f_i_not_zero = Condition("not", operand= condition_f_i_zero)

        current_node.instructions.append(f"{stab}_flag")
        current_node.branches = [
            Branch(target=f"r_{gen.index(stab)+1}_{stab}_s_f_all_zero", condition= condition_i_all_zero),
            Branch(target=f"r_{gen.index(stab)+1}_{stab}_s_not_zero_f_zero", condition= condition_and_s_i_not_zero_f_zero),
            Branch(target=f"r_{gen.index(stab)+1}_{stab}_f_not_zero", condition= condition_f_i_not_zero)
        ]
        protocol.add_node(current_node)

        node = Node(
                        node_id= f"r_{gen.index(stab)+1}_{stab}_s_not_zero_f_zero",
                        instructions=["raw_syndrome"],
                        branches=[Branch(target=f"ter_{gen.index(stab)*3}")]
                    )
        protocol.add_node(node)

        node = Node(node_id= f"ter_{gen.index(stab)*3}",
                        instructions=[f"LUT_" + "_".join([f"s_{i}_f_{i}" for i in range(gen.index(stab)+1)]) + f"_s_{gen.index(stab)+1}"],
                        branches=[]
                    )
        protocol.add_node(node)

        node = Node(
                        node_id= f"r_{gen.index(stab)+1}_{stab}_f_not_zero",
                        instructions=[f"{stab}_raw"],
                        branches=[Branch(target=f"after_r_{gen.index(stab)+1}_{stab}_raw",condition= None)]
                    )
        protocol.add_node(node)

        condition_s_i_zero = Condition(cond_type = "equal", left=f"s_{gen.index(stab)+2}", right=False)
        condition_s_i_not_zero = Condition("not", operand= condition_s_i_zero)
        node = Node( node_id= f"after_r_{gen.index(stab)+1}_{stab}_raw",
                        instructions=[f"{gen_2[gen.index(stab)]}_raw"],
                        branches=[Branch(target=f"r_{gen.index(stab)+2}_{gen_2[gen.index(stab)]}_s_not_zero", condition= condition_s_i_not_zero),
                                  Branch(target=f"r_{gen.index(stab)+2}_{gen_2[gen.index(stab)]}_s_zero", condition= condition_s_i_zero)]
                    )
        protocol.add_node(node)
       
        node = Node(
                        node_id= f"r_{gen.index(stab)+2}_{gen_2[gen.index(stab)]}_s_not_zero",
                        instructions=[f"{gen_3[gen.index(stab)][0]}_raw"],
                        branches=[Branch(target=f"ter_{gen.index(stab)*3 +1}")]
                    )
        protocol.add_node(node)

        node = Node(node_id = f"ter_{gen.index(stab)*3 +1}", 
                        instructions=[f"LUT_" + "_".join([f"s_{i}_f_{i}" for i in range(0,gen.index(stab)+1)])+"_"+"_".join([f"s_{i}" for i in range(gen.index(stab)+1,gen.index(stab)+4)]) ],
                        branches=[]
                    )
        protocol.add_node(node)

        node = Node(
                        node_id= f"r_{gen.index(stab)+2}_{gen_2[gen.index(stab)]}_s_zero",
                        instructions=[f"{gen_3[gen.index(stab)][1]}_raw"],
                        branches=[Branch(target=f"ter_{gen.index(stab)*3 +2}")]
                    )
        protocol.add_node(node)

        node = Node(node_id = f"ter_{gen.index(stab)*3 +2}", 
                        instructions=[f"LUT_" + "_".join([f"s_{i}_f_{i}" for i in range(0,gen.index(stab)+1)])+"_"+"_".join([f"s_{i}" for i in range(gen.index(stab)+1,gen.index(stab)+4)]) ],
                        branches=[]
                    )
        protocol.add_node(node)


        current_node = Node(node_id=f"r_{gen.index(stab)+1}_{stab}_s_f_all_zero",
            instructions=[],
            branches=[]
        )

        if stab == gen[-1]:
            print("last round")
            current_node.branches = [Branch(target = f"ter_final", condition = None)]
            current_node.instructions = []
            protocol.add_node(current_node)

           

            ter_final = Node(node_id=f"ter_final", instructions=["Break"], branches=[])
            protocol.add_node(ter_final)

        
    protocol.save_to_file("./protocols/5_1_3_low_depth_protocol.json")


def build_full_chris_d_5_protocol():
    import re

    protocol = Protocol(start_node="root")

    # -------------------------
    # Readable unique id generators
    # -------------------------
    node_ctr = 0
    ter_ctr = 0

    def _slug(s: str) -> str:
        # Keep letters/digits/_/-, replace everything else with '-'
        s = re.sub(r"[^A-Za-z0-9_-]+", "-", s)
        s = re.sub(r"-+", "-", s).strip("-")
        return s if s else "node"

    def nid(tag: str) -> str:
        nonlocal node_ctr
        node_ctr += 1
        return f"{_slug(tag)}#{node_ctr:03d}"

    def tid(tag: str = "ter") -> str:
        nonlocal ter_ctr
        ter_ctr += 1
        return f"{_slug(tag)}#{ter_ctr:03d}"

    # -------------------------
    # Subtree builder (unique prefix)
    # -------------------------
    def sub_tree_1(round: int, parent_id: str, parent_tag: str):
        """Attach subtree_1 branching logic to an EXISTING parent node."""
        if parent_id not in protocol.nodes:
            raise KeyError(f"parent node '{parent_id}' not found in protocol")

        # conditions
        condition_2_flag_r_i  = Condition("equal", left=f"f_{round}",   right=2)
        condition_1_flag_r_i  = Condition("equal", left=f"f_{round}",   right=1)
        condition_1_flag_r_i1 = Condition("equal", left=f"f_{round+1}", right=1)
        condition_0_flag_r_i1 = Condition("equal", left=f"f_{round+1}", right=0)
        condition_s_i_eq_s_i1 = Condition("equal", left=f"s_{round}", right=f"s_{round+1}")
        cond_s_i_neq_s_i1     = Condition("not", operand=condition_s_i_eq_s_i1)

        # allocate child ids ONCE
        f2_id = nid(f"{parent_tag}-r{round}-f2")
        f1_id = nid(f"{parent_tag}-r{round}-f1")

        # attach branches to parent
        protocol.nodes[parent_id].branches += [
            Branch(target=f2_id, condition=condition_2_flag_r_i),
            Branch(target=f1_id, condition=condition_1_flag_r_i),
        ]

        # --- f == 2 leaf -> LUT(s0,f0,...,s_round,f_round)
        t = tid("lut")
        print(t)
        protocol.add_node(Node(node_id=f2_id, instructions=["raw_syndrome"], branches=[Branch(target=t)]))

        instr = "LUT" + "".join(f"_s_{i}_f_{i}" for i in range(round + 1))
        protocol.add_node(Node(node_id=t, instructions=[instr+ f"_s_{round+1}"], branches=[]))

        # --- f == 1 -> run flag_syndrome then branch on round+1 info
        f1_next_f1 = nid(f"{parent_tag}-r{round+1}-f1")
        f1_seq     = nid(f"{parent_tag}-r{round+1}-s-eq")
        f1_sneq    = nid(f"{parent_tag}-r{round+1}-s-neq")

        protocol.add_node(Node(
            node_id=f1_id,
            instructions=["flag_syndrome"],
            branches=[
                Branch(target=f1_next_f1, condition=condition_1_flag_r_i1),
                Branch(target=f1_seq,     condition=Condition("and", operands=[condition_0_flag_r_i1, condition_s_i_eq_s_i1])),
                Branch(target=f1_sneq,    condition=Condition("and", operands=[condition_0_flag_r_i1, cond_s_i_neq_s_i1])),
            ]
        ))

        # --- round+1 f==1 leaf -> LUT up to round+1
        t = tid("lut")
      
        protocol.add_node(Node(node_id=f1_next_f1, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        instr2 = "LUT" + "".join(f"_s_{i}_f_{i}" for i in range(round + 2))
        protocol.add_node(Node(node_id=t, instructions=[instr2 +f"_s_{round+2}"], branches=[]))

        # --- s_eq leaf -> LUT up to round+1
        t = tid("lut")
        
        protocol.add_node(Node(node_id=f1_seq, instructions=[], branches=[Branch(target=t)]))
        protocol.add_node(Node(node_id=t, instructions=[instr2], branches=[]))

        # --- s_neq -> raw_syndrome -> LUT(..., s_{round+2})
        t = tid("lut")
        
        protocol.add_node(Node(node_id=f1_sneq, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        
        protocol.add_node(Node(node_id=t, instructions=[instr2 +f"_s_{round+2}"], branches=[]))

    def sub_tree_2(round: int, parent_id: str, parent_tag: str):
        """Attach subtree_2 branching logic to an EXISTING parent node."""
        if parent_id not in protocol.nodes:
            raise KeyError(f"parent node '{parent_id}' not found in protocol")

        # conditions
        condition_1_flag_r_i   = Condition("equal", left=f"f_{round}",   right=1)
        condition_0_flag_r_i   = Condition("equal", left=f"f_{round}",   right=0)
        condition_1_flag_r_i1  = Condition("equal", left=f"f_{round+1}", right=1)
        condition_0_flag_r_i1  = Condition("equal", left=f"f_{round+1}", right=0)
        condition_s_i_eq_s_i1  = Condition("equal", left=f"s_{round}", right=f"s_{round+1}")
        cond_s_i_neq_s_i1      = Condition("not", operand=condition_s_i_eq_s_i1)

        # allocate child ids ONCE
        f1_id = nid(f"{parent_tag}-r{round}-f1")
        f0_id = nid(f"{parent_tag}-r{round}-f0")

        # attach to parent
        protocol.nodes[parent_id].branches += [
            Branch(target=f1_id, condition=condition_1_flag_r_i),
            Branch(target=f0_id, condition=condition_0_flag_r_i),
        ]

        # --- f == 1 -> raw_syndrome -> LUT
        t = tid("lut")
        
        protocol.add_node(Node(node_id=f1_id, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        lut_name = "LUT" + "".join(f"_s_{i}_f_{i}" for i in range(round + 1))
        protocol.add_node(Node(node_id=t, instructions=[lut_name+ f"_s_{round+1}"], branches=[]))

        # --- f == 0 -> flag_syndrome -> branch
        r1_f1   = nid(f"{parent_tag}-r{round+1}-f1")
        r1_seq  = nid(f"{parent_tag}-r{round+1}-s-eq")
        r1_sneq = nid(f"{parent_tag}-r{round+1}-s-neq")

        protocol.add_node(Node(
            node_id=f0_id,
            instructions=["flag_syndrome"],
            branches=[
                Branch(target=r1_f1,   condition=condition_1_flag_r_i1),
                Branch(target=r1_seq,  condition=Condition("and", operands=[condition_0_flag_r_i1, condition_s_i_eq_s_i1])),
                Branch(target=r1_sneq, condition=Condition("and", operands=[condition_0_flag_r_i1, cond_s_i_neq_s_i1])),
            ]
        ))

        # --- round+1 f==1 leaf -> LUT up to round+1
        t = tid("lut")
      
        protocol.add_node(Node(node_id=r1_f1, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        instr2 = "LUT" + "".join(f"_s_{i}_f_{i}" for i in range(round + 2))
        
        protocol.add_node(Node(node_id=t, instructions=[instr2 + f"_s_{round+2}"], branches=[]))

        # --- s_eq leaf -> LUT up to round+1
        t = tid("lut")
        
        protocol.add_node(Node(node_id=r1_seq, instructions=[], branches=[Branch(target=t)]))
        protocol.add_node(Node(node_id=t, instructions=[instr2], branches=[]))

        # --- s_neq -> raw_syndrome -> LUT(..., s_{round+2})
        t = tid("lut")
        protocol.add_node(Node(node_id=r1_sneq, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
       
        protocol.add_node(Node(node_id=t, instructions=[instr2 + f"_s_{round+2}"], branches=[]))

    # -------------------------
    # Build root
    # -------------------------
    protocol.add_node(Node(node_id="root", instructions=["flag_syndrome"], branches=[]))

    # root branch: if NOT (f0==0) -> subtree_1 at round 0
    cond_f0_eq_0 = Condition("equal", left="f_0", right=0)
    cond_f0_neq_0 = Condition("not", operand=cond_f0_eq_0)
    r0_f0_id = nid("r0-f0")
    protocol.nodes["root"].branches.append(
        Branch(target=r0_f0_id, condition =cond_f0_eq_0))
    

    
    # attach subtree_1 for round 0 hanging off root
    sub_tree_1(0, "root", "fromRoot")

    # -------------------------
    # Build the r0_f0 node
    # -------------------------
    condition_s0_eq_s1  = Condition("equal", left="s_0", right="s_1")
    condition_s0_neq_s1 = Condition("not", operand=condition_s0_eq_s1)
    condition_f1_eq_0   = Condition("equal", left="f_1", right=0)

    s_eq_id  = nid("r0f0-f1eq0-s0eqs1")
    s_neq_id = nid("r0f0-f1eq0-s0neqs1")

    # create these nodes ONCE so later protocol.nodes[...] works
    protocol.add_node(Node(node_id=s_eq_id,  instructions=["flag_syndrome"], branches=[]))
    protocol.add_node(Node(node_id=s_neq_id, instructions=["flag_syndrome"], branches=[]))

    protocol.add_node(Node(
        node_id=r0_f0_id,
        instructions=["flag_syndrome"],
        branches=[
            Branch(target=s_eq_id,  condition=Condition("and", operands=[condition_f1_eq_0, condition_s0_eq_s1])),
            Branch(target=s_neq_id, condition=Condition("and", operands=[condition_f1_eq_0, condition_s0_neq_s1])),
        ]
    ))

    # attach subtrees below
    sub_tree_1(1, r0_f0_id, "fromR0F0")
    sub_tree_2(2, s_neq_id, "fromR0F0Sneq")
    sub_tree_1(2, s_eq_id,  "fromR0F0Seq")
    #sub_tree_1(2, s_eq_id,  "fromR0F0Seq")

    # -------------------------
    # Extend the s_eq_id node (UPDATE it; do not overwrite by add_node)
    # -------------------------
    condition_s1_eq_s2  = Condition("equal", left="s_1", right="s_2")
    condition_s1_neq_s2 = Condition("not", operand=condition_s1_eq_s2)
    condition_f2_eq_0   = Condition("equal", left="f_2", right=0)


    # --- build the two reachable child nodes and attach them to s_eq_id
    r2_f0_id   = nid("fromR0F0Seq_r2_f0")
    r2_sneq_id = nid("fromR0F0Seq_r2_sneq")

    # Do NOT overwrite existing branches on s_eq_id; extend them.
    protocol.nodes[s_eq_id].branches += [
        Branch(
            target=r2_f0_id,
            condition=Condition("and", operands=[condition_f2_eq_0, condition_s1_eq_s2])
        ),
        Branch(
            target=r2_sneq_id,
            condition=Condition("and", operands=[condition_f2_eq_0, condition_s1_neq_s2])
        ),
    ]

    # Ensure both target nodes exist exactly once
    if r2_f0_id not in protocol.nodes:
        protocol.add_node(Node(node_id=r2_f0_id, instructions=[], branches=[]))
    if r2_sneq_id not in protocol.nodes:
        protocol.add_node(Node(node_id=r2_sneq_id, instructions=['flag_syndrome'], branches=[]))

    # --- attach subtree_2 to the REAL reachable node id
    sub_tree_2(3, r2_sneq_id, "fromR0F0SeqR2Sneq")

    # --- attach LUT after r2_f0_id
    lut_term = tid("lut")
    # Do not overwrite any existing branches; append the terminal edge.
    protocol.nodes[r2_f0_id].branches += [Branch(target=lut_term, condition=None)]

    lut_name = "LUT" + "".join(f"_s_{i}_f_{i}" for i in range(0, 3))
    protocol.add_node(Node(node_id=lut_term, instructions=[lut_name], branches=[]))

        

    

    

    protocol.save_to_file("./protocols/full_chris_d_5_low_depth_protocol.json")
    return protocol


def build_correct_full_chris_d_5_protocol():
    import re

    protocol = Protocol(start_node="root")

    # -------------------------
    # Readable unique id generators
    # -------------------------
    node_ctr = 0
    ter_ctr = 0

    def _slug(s: str) -> str:
        # Keep letters/digits/_/-, replace everything else with '-'
        s = re.sub(r"[^A-Za-z0-9_-]+", "-", s)
        s = re.sub(r"-+", "-", s).strip("-")
        return s if s else "node"

    def nid(tag: str) -> str:
        nonlocal node_ctr
        node_ctr += 1
        return f"{_slug(tag)}#{node_ctr:03d}"

    def tid(tag: str = "ter") -> str:
        nonlocal ter_ctr
        ter_ctr += 1
        return f"{_slug(tag)}#{ter_ctr:03d}"

    # -------------------------
    # Subtree builder (unique prefix)
    # -------------------------
    def sub_tree_1(round: int, parent_id: str, parent_tag: str):
        """Attach subtree_1 branching logic to an EXISTING parent node."""
        if parent_id not in protocol.nodes:
            raise KeyError(f"parent node '{parent_id}' not found in protocol")

        # conditions
        
        condition_ri_f0  = Condition("equal", left=f"f_{round}",   right=0)
        condition_ri_f1  = Condition("equal", left=f"f_{round}",   right=1)

        # allocate child ids ONCE
        
        f1_id = nid(f"{parent_tag}-r{round}-f1")
        f0_id = nid(f"{parent_tag}-r{round}-f0")

        # attach branches to parent
        protocol.nodes[parent_id].branches.append(Branch(target=f1_id, condition=condition_ri_f1))
        protocol.nodes[parent_id].branches.append(Branch(target=f0_id, condition=condition_ri_f0))
        # --- f == 1 -> run flag_syndrome then branch on round+1 info
        
        ter = tid("ter")
        protocol.add_node(Node(
            node_id=f1_id,
            instructions=["raw_syndrome"],
            branches=[
                Branch(target=ter, condition=None),
            ]
        ))
        instr1 = "LUT" + f"_s_{round+1}"+ "".join(f"_f_{i}" for i in range(round+1))
        protocol.add_node(Node(node_id=ter, instructions=[instr1], branches=[]))

        ri1_f1= nid(f"{f0_id}-r{round+1}-f1")
        ri1_f0_seq     = nid(f"{f0_id}-r{round+1}-s-eq")
        ri1_f0_sneq    = nid(f"{f0_id}-r{round+1}-s-neq")

        condition_ri1_f_0 = Condition("equal", left=f"f_{round+1}", right=0)
        condition_ri1_f_1 = Condition("equal", left=f"f_{round+1}", right=1)
        condition_s_i_eq_s_i1 = Condition("equal", left=f"s_{round}", right=f"s_{round+1}")
        cond_s_i_neq_s_i1     = Condition("not", operand=condition_s_i_eq_s_i1) 

        protocol.add_node(Node(
            node_id=f0_id,
            instructions=["flag_syndrome"],
            branches=[
                Branch(target=ri1_f1, condition= condition_ri1_f_1),
                Branch(target=ri1_f0_seq,     condition=Condition("and", operands=[condition_ri1_f_0, condition_s_i_eq_s_i1])),
                Branch(target=ri1_f0_sneq,    condition=Condition("and", operands=[condition_ri1_f_0, cond_s_i_neq_s_i1])),
            ]
        ))

        # --- round+1 f==1 leaf -> LUT up to round+1
        t = tid("lut")
      
        protocol.add_node(Node(node_id=ri1_f1, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        instr2 = "LUT" + f"_s_{round+2}" + "".join(f"_f_{i}" for i in range(round+2))
        protocol.add_node(Node(node_id=t, instructions=[instr2], branches=[]))

        # --- s_eq leaf -> LUT up to round+1
        t = tid("lut")
        instr3 = "LUT" + f"_s_{round+1}" + "".join(f"_f_{i}" for i in range(round+2))
        protocol.add_node(Node(node_id= ri1_f0_seq, instructions=[], branches=[Branch(target=t)]))
        protocol.add_node(Node(node_id=t, instructions=[instr3], branches=[]))

        # --- s_neq -> raw_syndrome -> LUT(..., s_{round+2})
        t = tid("lut")
        instr4 = "LUT" + f"_s_{round+2}" + "".join(f"_f_{i}" for i in range(round+2))
        protocol.add_node(Node(node_id= ri1_f0_sneq, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        
        protocol.add_node(Node(node_id=t, instructions=[instr4], branches=[]))

    def sub_tree_2(round: int, parent_id: str, parent_tag: str):
        """Attach subtree_2 branching logic to an EXISTING parent node."""
        if parent_id not in protocol.nodes:
            raise KeyError(f"parent node '{parent_id}' not found in protocol")

        # conditions
        condition_1_flag_r_i   = Condition("equal", left=f"f_{round}",   right=1)
        condition_0_flag_r_i   = Condition("equal", left=f"f_{round}",   right=0)
        condition_1_flag_r_i1  = Condition("equal", left=f"f_{round+1}", right=1)
        condition_0_flag_r_i1  = Condition("equal", left=f"f_{round+1}", right=0)
        condition_s_i_eq_s_i1  = Condition("equal", left=f"s_{round}", right=f"s_{round+1}")
        cond_s_i_neq_s_i1      = Condition("not", operand=condition_s_i_eq_s_i1)

        # allocate child ids ONCE
        f1_id = nid(f"{parent_tag}-r{round}-f1")
        f0_id = nid(f"{parent_tag}-r{round}-f0")

        # attach to parent
        protocol.nodes[parent_id].branches += [
            Branch(target=f1_id, condition=condition_1_flag_r_i),
            Branch(target=f0_id, condition=condition_0_flag_r_i),
        ]

        # --- f == 1 -> raw_syndrome -> LUT
        t = tid("lut")
        
        protocol.add_node(Node(node_id=f1_id, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        lut_name = "LUT" + f"_s_{round + 1}"
        protocol.add_node(Node(node_id=t, instructions=[lut_name+ f"_s_{round+1}"], branches=[]))

        # --- f == 0 -> flag_syndrome -> branch
        r1_f1   = nid(f"{parent_tag}-r{round+1}-f1")
        r1_seq  = nid(f"{parent_tag}-r{round+1}-s-eq")
        r1_sneq = nid(f"{parent_tag}-r{round+1}-s-neq")

        protocol.add_node(Node(
            node_id=f0_id,
            instructions=["flag_syndrome"],
            branches=[
                Branch(target=r1_f1,   condition=condition_1_flag_r_i1),
                Branch(target=r1_seq,  condition=Condition("and", operands=[condition_0_flag_r_i1, condition_s_i_eq_s_i1])),
                Branch(target=r1_sneq, condition=Condition("and", operands=[condition_0_flag_r_i1, cond_s_i_neq_s_i1])),
            ]
        ))

        # --- round+1 f==1 leaf -> LUT up to round+1
        t = tid("lut")
      
        protocol.add_node(Node(node_id=r1_f1, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
        instr2 =  "LUT" + f"_s_{round + 1}"
        
        protocol.add_node(Node(node_id=t, instructions=[instr2], branches=[]))

        # --- s_eq leaf -> LUT up to round+1
        t = tid("lut")
        instr3 =  "LUT" + f"_s_{round }"
        protocol.add_node(Node(node_id=r1_seq, instructions=[], branches=[Branch(target=t)]))
        protocol.add_node(Node(node_id=t, instructions=[instr3], branches=[]))

        # --- s_neq -> raw_syndrome -> LUT(..., s_{round+2})
        t = tid("lut")
        instr4 =  "LUT" + f"_s_{round + 1}"
        protocol.add_node(Node(node_id=r1_sneq, instructions=["raw_syndrome"], branches=[Branch(target=t)]))
       
        protocol.add_node(Node(node_id=t, instructions=[instr4], branches=[]))

    # -------------------------
    # Build root
    # -------------------------
    protocol.add_node(Node(node_id="root", instructions=["flag_syndrome"], branches=[]))

    # root branch: if NOT (f0==0) -> subtree_1 at round 0
    cond_f0_eq_0 = Condition("equal", left="f_0", right=0)
    cond_f0_eq_1 = Condition("equal", left="f_0", right=1)
    r0_f0_id = nid("r0_f0-r1")
    r0_f1_id = nid("r0_f1-r1")
    protocol.nodes["root"].branches += [
        Branch(target=r0_f0_id, condition =cond_f0_eq_0),
        Branch(target=r0_f1_id, condition=cond_f0_eq_1)
    ]
    

    protocol.add_node(Node(
        node_id=r0_f1_id,
        instructions=["flag_syndrome"],
        branches=[]
    ))  
    sub_tree_1(1, r0_f1_id, r0_f1_id)  

    r0_f0_r1_f1 = nid("r0_f0-r1_f1")
    r0_f0_r1_f0_s0eq_s1_id = nid("r0_f0-r1_f0-s0eqs1")
    r0_f0_r1_f0_s0neq_s1_id = nid("r0_f0-r1_f0-s0neqs1")

    condition_s0_eq_s1  = Condition("equal", left="s_0", right="s_1")
    condition_s0_neq_s1 = Condition("not", operand=condition_s0_eq_s1)
    condition_f1_eq_0   = Condition("equal", left="f_1", right=0)
    condition_f1_eq_1  = Condition("equal", left="f_1", right=1)

    protocol.add_node(Node(
        node_id=r0_f0_id,       
        instructions=["flag_syndrome"],
        branches=[Branch(target=r0_f0_r1_f1, condition= condition_f1_eq_1),
                  Branch(target=r0_f0_r1_f0_s0eq_s1_id, condition=Condition("and", operands=[condition_f1_eq_0, condition_s0_eq_s1])),
                  Branch(target=r0_f0_r1_f0_s0neq_s1_id, condition=Condition("and", operands=[condition_f1_eq_0, condition_s0_neq_s1])),
                 ]
    ))

    protocol.add_node(Node(
        node_id=r0_f0_r1_f1,
        instructions=["flag_syndrome"],
        branches=[]
    ))

    sub_tree_1(2, r0_f0_r1_f1, r0_f0_r1_f1)

    protocol.add_node(Node(
        node_id=r0_f0_r1_f0_s0neq_s1_id,
        instructions=["flag_syndrome"],
        branches=[]
    ))
    sub_tree_1(2, r0_f0_r1_f0_s0neq_s1_id, r0_f0_r1_f0_s0neq_s1_id)

    protocol.add_node(Node(
        node_id=r0_f0_r1_f0_s0eq_s1_id,
        instructions=["flag_syndrome"],
        branches=[]
    ))

    r0_f0_r1_f0_s0eq_s1_s1_eq_s2_id = nid("r0_f0-r1_f0-s0eqs1-r2_f0_s1eqs2")
    r0_f0_r1_f0_s0eq_s1_s1_neq_s2_id = nid("r0_f0-r1_f0-s0eqs1-r2_f0_s1neqs2")
    r0_f0_r1_f0_s0eq_s1_r2_f1_id = nid("r0_f0-r1_f0-s0eqs1-r2_f1")

    condition_s1_eq_s2  = Condition("equal", left="s_1", right="s_2") 
    condition_s1_neq_s2 = Condition("not", operand=condition_s1_eq_s2)
    condition_f2_eq_0   = Condition("equal", left="f_2", right=0)
    condition_f2_eq_1   = Condition("equal", left="f_2", right=1)

    protocol.nodes[r0_f0_r1_f0_s0eq_s1_id].branches += [
        Branch(target=r0_f0_r1_f0_s0eq_s1_r2_f1_id, condition= condition_f2_eq_1),
        Branch(
            target=r0_f0_r1_f0_s0eq_s1_s1_eq_s2_id,
            condition=Condition("and", operands=[condition_f2_eq_0, condition_s1_eq_s2])
        ),
        Branch(
            target=r0_f0_r1_f0_s0eq_s1_s1_neq_s2_id,
            condition=Condition("and", operands=[condition_f2_eq_0, condition_s1_neq_s2])
        ),
    ] 
    protocol.add_node(Node(
        node_id=r0_f0_r1_f0_s0eq_s1_r2_f1_id,
        instructions=["flag_syndrome"],
        branches=[]
    ))
    sub_tree_1(3, r0_f0_r1_f0_s0eq_s1_r2_f1_id, r0_f0_r1_f0_s0eq_s1_r2_f1_id)
    protocol.add_node(Node(
        node_id=r0_f0_r1_f0_s0eq_s1_s1_neq_s2_id,
        instructions=['flag_syndrome'],
        branches=[]
    ))
    sub_tree_1(3, r0_f0_r1_f0_s0eq_s1_s1_neq_s2_id, r0_f0_r1_f0_s0eq_s1_s1_neq_s2_id)
    
    ter = tid("lut")
    protocol.add_node(Node(
        node_id=r0_f0_r1_f0_s0eq_s1_s1_eq_s2_id,
        instructions=[],
        branches=[Branch(target=ter, condition=[])]
    ))
    lut_name = "LUT" +"_s_2" + "".join(f"_f_{i}" for i in range(0,3))
    protocol.add_node(Node(node_id=ter, instructions=[lut_name], branches=[]))


    

    
    
    

    
    

    

    protocol.save_to_file("./protocols/correct_full_chris_d_5_low_depth_protocol.json")
    return protocol






