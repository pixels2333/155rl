"""从 configs/offsets.yaml 生成 Bridge DLL 使用的 C 头文件。

单一事实来源:offsets.yaml -> src/bridge/offsets_generated.h
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "configs" / "offsets.yaml"
OUT = ROOT / "src" / "bridge" / "offsets_generated.h"

TEMPLATE = """// 自动生成,请勿手改:scripts/gen_offsets_header.py <- configs/offsets.yaml
#ifndef TH155_OFFSETS_GENERATED_H
#define TH155_OFFSETS_GENERATED_H

#define TH155_EXPECTED_SIZE   {exe_size}u
#define TH155_EXPECTED_SHA256 "{sha}"

// --- engine ---
#define O_VM_GLOBAL_RVA       {vm_global_rva:#010x}u
#define O_VM_ROOTTABLE        {vm_roottable_offset:#010x}u
#define O_FRAME_COUNTER_RVA   {frame_counter_rva:#010x}u

// --- squirrel ---
#define O_RAW_TYPE_MASK       {raw_type_mask:#010x}u
#define O_TAG_NULL            {tag_null:#010x}u
#define O_TAG_INT             {tag_int:#010x}u
#define O_TAG_FLOAT           {tag_float:#010x}u
#define O_TAG_BOOL            {tag_bool:#010x}u
#define O_TAG_STRING          {tag_string:#010x}u
#define O_TAG_TABLE           {tag_table:#010x}u
#define O_TAG_ARRAY           {tag_array:#010x}u
#define O_TAG_INSTANCE        {tag_instance:#010x}u
#define O_TAG_CLOSURE         {tag_closure:#010x}u
#define O_OBJ_VALUE           0x04u

#define O_STR_LENGTH          {s_len:#010x}u
#define O_STR_DATA            {s_data:#010x}u

#define O_TABLE_NODES         {t_nodes:#010x}u
#define O_TABLE_NUMNODES      {t_numnodes:#010x}u
#define O_TABLE_NODE_STRIDE   {t_stride:#010x}u
#define O_TABLE_NODE_VTAG     0x00u
#define O_TABLE_NODE_VALUE    0x04u
#define O_TABLE_NODE_KTAG     0x08u
#define O_TABLE_NODE_KEY      0x0Cu
#define O_TABLE_NODE_NEXT     0x10u

#define O_ARRAY_VALUES        {a_values:#010x}u
#define O_ARRAY_SIZE          {a_size:#010x}u

#define O_INST_CLASS          {i_class:#010x}u
#define O_INST_USERPTR        {i_userptr:#010x}u
#define O_INST_VALUES         {i_values:#010x}u
#define O_INST_STRIDE         {i_stride:#010x}u

#define O_CLASS_MEMBERS       {c_members:#010x}u
#define O_CLASS_DEFAULTS      {c_defaults:#010x}u
#define O_CLASS_METHODS       {c_methods:#010x}u
#define O_MEMBER_FIELD_FLAG   {m_field:#010x}u
#define O_MEMBER_METHOD_FLAG  {m_method:#010x}u

// --- native: Manbow::Actor2D ---
#define O_ACTOR_X             {n_x:#010x}u
#define O_ACTOR_Y             {n_y:#010x}u
#define O_ACTOR_ID            {n_id:#010x}u
#define O_ACTOR_VX            {n_vx:#010x}u
#define O_ACTOR_VY            {n_vy:#010x}u
#define O_ACTOR_DIR           {n_dir:#010x}u
#define O_ACTOR_ANIM          {n_anim:#010x}u
#define O_ACTOR_RX            {n_rx:#010x}u
#define O_ACTOR_RY            {n_ry:#010x}u
#define O_ACTOR_RZ            {n_rz:#010x}u
#define O_ACTOR_SX            {n_sx:#010x}u
#define O_ACTOR_SY            {n_sy:#010x}u
#define O_ACTOR_SZ            {n_sz:#010x}u
#define O_ACTOR_GROUP         {n_group:#010x}u
#define O_ACTOR_CGROUP        {n_cgroup:#010x}u
#define O_ACTOR_CMASK         {n_cmask:#010x}u
#define O_ACTOR_CBGROUP       {n_cbgroup:#010x}u
#define O_ACTOR_CBMASK        {n_cbmask:#010x}u
#define O_ACTOR_BOX_L         {n_box_l:#010x}u
#define O_ACTOR_BOX_T         {n_box_t:#010x}u
#define O_ACTOR_BOX_R         {n_box_r:#010x}u
#define O_ACTOR_BOX_B         {n_box_b:#010x}u
#define O_ACTOR_HIT_L         {n_hit_l:#010x}u
#define O_ACTOR_HIT_T         {n_hit_t:#010x}u
#define O_ACTOR_HIT_R         {n_hit_r:#010x}u
#define O_ACTOR_HIT_B         {n_hit_b:#010x}u

#define O_ANIM_MOTION         {an_motion:#010x}u
#define O_ANIM_TAKE           {an_take:#010x}u
#define O_ANIM_FRAME          {an_frame:#010x}u

// --- native: Manbow::InputMulti ---
#define O_INPUT_X             {in_x:#010x}u
#define O_INPUT_Y             {in_y:#010x}u
#define O_INPUT_B0            {in_b0:#010x}u
#define O_INPUT_B1            {in_b1:#010x}u
#define O_INPUT_B2            {in_b2:#010x}u
#define O_INPUT_B3            {in_b3:#010x}u
#define O_INPUT_B4            {in_b4:#010x}u
#define O_INPUT_B5            {in_b5:#010x}u
#define O_INPUT_B10           {in_b10:#010x}u
#define O_INPUT_UPDATE_RVA    {in_update_rva:#010x}u
#define O_INPUT_VTABLE_RVA    {in_vtable_rva:#010x}u

// --- native: Manbow::InputCommand ---
#define O_CMD_B0              {cm_b0:#010x}u
#define O_CMD_B1              {cm_b1:#010x}u
#define O_CMD_B2              {cm_b2:#010x}u
#define O_CMD_B3              {cm_b3:#010x}u
#define O_CMD_B4              {cm_b4:#010x}u
#define O_CMD_B5              {cm_b5:#010x}u

// --- native: Actor2DProcGroup (弹幕容器) ---
#define O_PG_HEAD             {pg_head:#010x}u
#define O_PG_NEXT             {pg_next:#010x}u
#define O_PG_PREV             {pg_prev:#010x}u
#define O_PG_PTR              {pg_ptr:#010x}u

#endif // TH155_OFFSETS_GENERATED_H
"""


def main() -> int:
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    sq = cfg["squirrel"]
    tg = sq["type_tags"]
    n = cfg["native"]
    a2d, anim = n["actor2d"], n["animation_controller"]
    im, ic = n["input_multi"], n["input_command"]
    pg = n["actor2d_proc_group"]
    vals = dict(
        exe_size=cfg["meta"]["exe_size"],
        sha=cfg["meta"]["exe_sha256"],
        vm_global_rva=cfg["engine"]["vm_global_rva"],
        vm_roottable_offset=cfg["engine"]["vm_roottable_offset"],
        frame_counter_rva=cfg["engine"]["frame_counter_rva"],
        raw_type_mask=sq["raw_type_mask"],
        s_len=sq["string"]["length"], s_data=sq["string"]["data_offset"],
        tag_null=(tg.get("null") if tg.get("null") is not None else tg[None]), tag_int=tg["integer"], tag_float=tg["float"],
        tag_bool=tg["bool"], tag_string=tg["string"], tag_table=tg["table"],
        tag_array=tg["array"], tag_instance=tg["instance"], tag_closure=tg["closure"],
        t_nodes=sq["table"]["nodes"], t_numnodes=sq["table"]["num_nodes"],
        t_stride=sq["table"]["node_stride"],
        a_values=sq["array"]["values"], a_size=sq["array"]["size"],
        i_class=sq["instance"]["class"], i_userptr=sq["instance"]["userpointer"],
        i_values=sq["instance"]["values"], i_stride=sq["instance"]["value_stride"],
        c_members=sq["class"]["members"], c_defaults=sq["class"]["default_values"],
        c_methods=sq["class"]["methods"],
        m_field=sq["class"]["member_field_flag"], m_method=sq["class"]["member_method_flag"],
        n_x=a2d["x"], n_y=a2d["y"], n_id=a2d["id"], n_vx=a2d["vx"], n_vy=a2d["vy"],
        n_dir=a2d["direction"], n_anim=a2d["animation"], n_rx=a2d["rx"], n_ry=a2d["ry"],
        n_rz=a2d.get("rz", 0x64), n_sx=a2d.get("sx", 0x50), n_sy=a2d.get("sy", 0x54), n_sz=a2d.get("sz", 0x58),
        n_group=a2d["group"], n_cgroup=a2d["collision_group"],
        n_cmask=a2d["collision_mask"], n_cbgroup=a2d["callback_group"],
        n_cbmask=a2d["callback_mask"], n_hit_l=a2d["hit_left"],
        n_hit_t=a2d["hit_top"], n_hit_r=a2d["hit_right"], n_hit_b=a2d["hit_bottom"],
        n_box_l=a2d["box_left"], n_box_t=a2d["box_top"],
        n_box_r=a2d["box_right"], n_box_b=a2d["box_bottom"],
        an_motion=anim["motion"], an_take=anim["key_take"], an_frame=anim["key_frame"],
        in_x=im["x"], in_y=im["y"], in_b0=im["b0"], in_b1=im["b1"], in_b2=im["b2"],
        in_b3=im["b3"], in_b4=im["b4"], in_b5=im["b5"], in_b10=im["b10"],
        in_update_rva=im["update_rva"], in_vtable_rva=im["vtable_rva"],
        cm_b0=ic["b0"], cm_b1=ic["b1"], cm_b2=ic["b2"], cm_b3=ic["b3"],
        cm_b4=ic["b4"], cm_b5=ic["b5"],
        pg_head=pg["head_sentinel"], pg_next=pg["node_next"],
        pg_prev=pg["node_prev"], pg_ptr=pg["node_ptr"],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEMPLATE.format(**vals), encoding="utf-8")
    print(f"[ok] {OUT} ({len(TEMPLATE)} bytes template)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
