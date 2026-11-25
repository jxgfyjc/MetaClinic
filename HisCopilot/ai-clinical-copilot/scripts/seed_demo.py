"""Seed the MVP Sandbox with demo data — instruct #51/#52.

All data is DEMO ONLY (synthetic, no real PII). Seeds:
  - MockHISAdapter in-memory: patients, encounters, drugs, hospital formulas
  - SQLite knowledge_chunks + vec0: clinical/TCM guidelines for RAG

At least 5 cases per instruct #52:
  1. respiratory  (急性上呼吸道感染 — 西医为主)
  2. digestive    (慢性胃炎 — 中西医结合)
  3. metabolic-chronic (2型糖尿病 — 慢病管理)
  4. tcm-typical  (失眠 — 中医辨证为主)
  5. high-risk-followup (高血压急症风险随访 — 高危随访)
"""
from __future__ import annotations

import json
import uuid
from typing import Any

DEMO_DOCTORS = [
    {"id": "dr-001", "name": "张明 主任医师", "role": "DOCTOR", "dept": "全科/中西医结合",
     "license": "DEMO-110101201900001", "can_sign": True, "demo_only": True},
    {"id": "dr-002", "name": "李秀英 副主任医师", "role": "DOCTOR", "dept": "中医科",
     "license": "DEMO-110101201900002", "can_sign": True, "demo_only": True},
    {"id": "dr-003", "name": "王强 主治医师", "role": "DOCTOR", "dept": "内科",
     "license": "DEMO-110101201900003", "can_sign": True, "demo_only": True},
]

DEMO_NURSE = {"id": "nu-001", "name": "陈丽 主管护师", "role": "NURSE",
              "license": "DEMO-110101201900004", "demo_only": True}

# 10-20 demo drugs (instruct #51), clearly DEMO
DEMO_DRUGS = [
    {"id": "drug-001", "name": "对乙酰氨基酚片", "generic_name": "Paracetamol",
     "category": "western", "form": "片剂", "strength": "0.5g", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-002", "name": "阿莫西林胶囊", "generic_name": "Amoxicillin",
     "category": "western", "form": "胶囊", "strength": "0.25g", "unit": "粒",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-003", "name": "氨氯地平片", "generic_name": "Amlodipine",
     "category": "western", "form": "片剂", "strength": "5mg", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-004", "name": "二甲双胍片", "generic_name": "Metformin",
     "category": "western", "form": "片剂", "strength": "0.5g", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-005", "name": "奥美拉唑肠溶胶囊", "generic_name": "Omeprazole",
     "category": "western", "form": "胶囊", "strength": "20mg", "unit": "粒",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-006", "name": "复方甘草片", "generic_name": "Compound Liquorice",
     "category": "western", "form": "片剂", "strength": "复方", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-007", "name": "氢氯噻嗪片", "generic_name": "Hydrochlorothiazide",
     "category": "western", "form": "片剂", "strength": "25mg", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-008", "name": "硝苯地平控释片", "generic_name": "Nifedipine",
     "category": "western", "form": "片剂", "strength": "30mg", "unit": "片",
     "hospital_dict": True, "demo_only": True},
    # TCM herbs (饮片)
    {"id": "drug-101", "name": "黄芪", "generic_name": "Astragali Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-102", "name": "党参", "generic_name": "Codonopsis Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-103", "name": "白术", "generic_name": "Atractylodis Macrocephalae Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-104", "name": "茯苓", "generic_name": "Poria",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-105", "name": "酸枣仁", "generic_name": "Ziziphi Spinosae Semen",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-106", "name": "川芎", "generic_name": "Chuanxiong Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-107", "name": "丹参", "generic_name": "Salviae Miltiorrhizae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-108", "name": "半夏", "generic_name": "Pinelliae Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "9g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-109", "name": "黄连", "generic_name": "Coptidis Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "3g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-110", "name": "肉桂", "generic_name": "Cinnamomi Cortex",
     "category": "tcm-herb", "form": "饮片", "strength": "3g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    # V1.2 §3: expanded drug library (~80+ entries total)
    # — Western drugs (40+) —
    {"id": "drug-009", "name": "阿托伐他汀钙片", "generic_name": "Atorvastatin",
     "category": "western", "form": "片剂", "strength": "20mg", "unit": "片",
     "usage": "口服 睡前一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-010", "name": "瑞舒伐他汀钙片", "generic_name": "Rosuvastatin",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-011", "name": "氯吡格雷片", "generic_name": "Clopidogrel",
     "category": "western", "form": "片剂", "strength": "75mg", "unit": "片",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-012", "name": "阿司匹林肠溶片", "generic_name": "Aspirin",
     "category": "western", "form": "片剂", "strength": "100mg", "unit": "片",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-013", "name": "美托洛尔缓释片", "generic_name": "Metoprolol",
     "category": "western", "form": "缓释片", "strength": "47.5mg", "unit": "片",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-014", "name": "缬沙坦胶囊", "generic_name": "Valsartan",
     "category": "western", "form": "胶囊", "strength": "80mg", "unit": "粒",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-015", "name": "厄贝沙坦片", "generic_name": "Irbesartan",
     "category": "western", "form": "片剂", "strength": "150mg", "unit": "片",
     "usage": "口服 每日一次", "hospital_dict": True, "demo_only": True},
    {"id": "drug-016", "name": "雷贝拉唑钠肠溶片", "generic_name": "Rabeprazole",
     "category": "western", "form": "肠溶片", "strength": "20mg", "unit": "片",
     "usage": "口服 每日一次晨起", "hospital_dict": True, "demo_only": True},
    {"id": "drug-017", "name": "铝碳酸镁咀嚼片", "generic_name": "Hydrotalcite",
     "category": "western", "form": "咀嚼片", "strength": "0.5g", "unit": "片",
     "usage": "饭后1-2小时咀嚼", "hospital_dict": True, "demo_only": True},
    {"id": "drug-018", "name": "莫沙必利片", "generic_name": "Mosapride",
     "category": "western", "form": "片剂", "strength": "5mg", "unit": "片",
     "usage": "饭前口服 tid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-019", "name": "多潘立酮片", "generic_name": "Domperidone",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "饭前口服 tid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-020", "name": "格列美脲片", "generic_name": "Glimepiride",
     "category": "western", "form": "片剂", "strength": "2mg", "unit": "片",
     "usage": "早餐前口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-021", "name": "西格列汀片", "generic_name": "Sitagliptin",
     "category": "western", "form": "片剂", "strength": "100mg", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-022", "name": "达格列净片", "generic_name": "Dapagliflozin",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "口服 qd 晨起", "hospital_dict": True, "demo_only": True},
    {"id": "drug-023", "name": "甘精胰岛素注射液", "generic_name": "Insulin Glargine",
     "category": "western", "form": "注射液", "strength": "300U/ml", "unit": "支",
     "usage": "皮下注射 睡前", "hospital_dict": True, "demo_only": True},
    {"id": "drug-024", "name": "左甲状腺素钠片", "generic_name": "Levothyroxine",
     "category": "western", "form": "片剂", "strength": "50μg", "unit": "片",
     "usage": "早餐前口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-025", "name": "甲巯咪唑片", "generic_name": "Thiamazole",
     "category": "western", "form": "片剂", "strength": "5mg", "unit": "片",
     "usage": "口服 tid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-026", "name": "别嘌醇片", "generic_name": "Allopurinol",
     "category": "western", "form": "片剂", "strength": "100mg", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-027", "name": "非布司他片", "generic_name": "Febuxostat",
     "category": "western", "form": "片剂", "strength": "40mg", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-028", "name": "秋水仙碱片", "generic_name": "Colchicine",
     "category": "western", "form": "片剂", "strength": "0.5mg", "unit": "片",
     "usage": "急性期口服", "hospital_dict": True, "demo_only": True},
    {"id": "drug-029", "name": "塞来昔布胶囊", "generic_name": "Celecoxib",
     "category": "western", "form": "胶囊", "strength": "200mg", "unit": "粒",
     "usage": "口服 bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-030", "name": "双氯芬酸钠肠溶片", "generic_name": "Diclofenac",
     "category": "western", "form": "肠溶片", "strength": "25mg", "unit": "片",
     "usage": "口服 tid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-031", "name": "盐酸氨基葡萄糖胶囊", "generic_name": "Glucosamine",
     "category": "western", "form": "胶囊", "strength": "0.24g", "unit": "粒",
     "usage": "口服 tid 饭时", "hospital_dict": True, "demo_only": True},
    {"id": "drug-032", "name": "头孢氨苄胶囊", "generic_name": "Cephalexin",
     "category": "western", "form": "胶囊", "strength": "0.25g", "unit": "粒",
     "usage": "口服 qid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-033", "name": "头孢克洛缓释片", "generic_name": "Cefaclor",
     "category": "western", "form": "缓释片", "strength": "0.375g", "unit": "片",
     "usage": "口服 bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-034", "name": "左氧氟沙星片", "generic_name": "Levofloxacin",
     "category": "western", "form": "片剂", "strength": "0.5g", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-035", "name": "阿奇霉素片", "generic_name": "Azithromycin",
     "category": "western", "form": "片剂", "strength": "0.25g", "unit": "片",
     "usage": "口服 qd 连服3天", "hospital_dict": True, "demo_only": True},
    {"id": "drug-036", "name": "孟鲁司特钠片", "generic_name": "Montelukast",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "睡前口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-037", "name": "沙丁胺醇气雾剂", "generic_name": "Salbutamol",
     "category": "western", "form": "气雾剂", "strength": "100μg/喷", "unit": "瓶",
     "usage": "按需吸入", "hospital_dict": True, "demo_only": True},
    {"id": "drug-038", "name": "布地奈德福莫特罗吸入剂", "generic_name": "Budesonide/Formoterol",
     "category": "western", "form": "吸入剂", "strength": "160/4.5μg", "unit": "支",
     "usage": "吸入 bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-039", "name": "盐酸氟西汀胶囊", "generic_name": "Fluoxetine",
     "category": "western", "form": "胶囊", "strength": "20mg", "unit": "粒",
     "usage": "口服 qd 晨起", "hospital_dict": True, "demo_only": True},
    {"id": "drug-040", "name": "草酸艾司西酞普兰片", "generic_name": "Escitalopram",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-041", "name": "酒石酸唑吡坦片", "generic_name": "Zolpidem",
     "category": "western", "form": "片剂", "strength": "10mg", "unit": "片",
     "usage": "睡前口服", "hospital_dict": True, "demo_only": True},
    {"id": "drug-042", "name": "右佐匹克隆片", "generic_name": "Dexzopiclone",
     "category": "western", "form": "片剂", "strength": "3mg", "unit": "片",
     "usage": "睡前口服", "hospital_dict": True, "demo_only": True},
    {"id": "drug-043", "name": "甲钴胺片", "generic_name": "Mecobalamin",
     "category": "western", "form": "片剂", "strength": "0.5mg", "unit": "片",
     "usage": "口服 tid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-044", "name": "普瑞巴林胶囊", "generic_name": "Pregabalin",
     "category": "western", "form": "胶囊", "strength": "75mg", "unit": "粒",
     "usage": "口服 bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-045", "name": "碳酸钙D3片", "generic_name": "Calcium Carbonate+D3",
     "category": "western", "form": "片剂", "strength": "600mg+125IU", "unit": "片",
     "usage": "口服 bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-046", "name": "骨化三醇胶囊", "generic_name": "Calcitriol",
     "category": "western", "form": "胶囊", "strength": "0.25μg", "unit": "粒",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    {"id": "drug-047", "name": "螺内酯片", "generic_name": "Spironolactone",
     "category": "western", "form": "片剂", "strength": "20mg", "unit": "片",
     "usage": "口服 qd-bid", "hospital_dict": True, "demo_only": True},
    {"id": "drug-048", "name": "呋塞米片", "generic_name": "Furosemide",
     "category": "western", "form": "片剂", "strength": "20mg", "unit": "片",
     "usage": "口服 qd", "hospital_dict": True, "demo_only": True},
    # — TCM herbs (40+) —
    {"id": "drug-111", "name": "甘草", "generic_name": "Glycyrrhizae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-112", "name": "当归", "generic_name": "Angelicae Sinensis Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-113", "name": "白芍", "generic_name": "Paeoniae Radix Alba",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-114", "name": "熟地黄", "generic_name": "Rehmanniae Radix Praeparata",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-115", "name": "生地黄", "generic_name": "Rehmanniae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-116", "name": "柴胡", "generic_name": "Bupleuri Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-117", "name": "枳壳", "generic_name": "Aurantii Fructus",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-118", "name": "陈皮", "generic_name": "Citri Reticulatae Pericarpium",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-119", "name": "木香", "generic_name": "Aucklandiae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-120", "name": "砂仁", "generic_name": "Amomi Fructus",
     "category": "tcm-herb", "form": "饮片", "strength": "3g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-121", "name": "山药", "generic_name": "Dioscoreae Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-122", "name": "薏苡仁", "generic_name": "Coicis Semen",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-123", "name": "泽泻", "generic_name": "Alismatis Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-124", "name": "车前子", "generic_name": "Plantaginis Semen",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-125", "name": "连翘", "generic_name": "Forsythiae Fructus",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-126", "name": "金银花", "generic_name": "Lonicerae Japonicae Flos",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-127", "name": "板蓝根", "generic_name": "Isatidis Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-128", "name": "桔梗", "generic_name": "Platycodonis Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-129", "name": "薄荷", "generic_name": "Menthae Haplocalycis Herba",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-130", "name": "菊花", "generic_name": "Chrysanthemi Flos",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-131", "name": "天麻", "generic_name": "Gastrodiae Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-132", "name": "钩藤", "generic_name": "Uncariae Ramulus cum Uncis",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-133", "name": "石决明", "generic_name": "Haliotidis Concha",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-134", "name": "龙骨", "generic_name": "Longgu",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-135", "name": "牡蛎", "generic_name": "Concha Ostreae",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-136", "name": "远志", "generic_name": "Polygalae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-137", "name": "茯神", "generic_name": "Poria cum Ligno",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-138", "name": "柏子仁", "generic_name": "Platycladi Semen",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-139", "name": "麦冬", "generic_name": "Ophiopogonis Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-140", "name": "五味子", "generic_name": "Schisandrae Chinensis Fructus",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-141", "name": "太子参", "generic_name": "Pseudostellariae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-142", "name": "玄参", "generic_name": "Scrophulariae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-143", "name": "牡丹皮", "generic_name": "Moutan Cortex",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-144", "name": "赤芍", "generic_name": "Paeoniae Radix Rubra",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-145", "name": "桃仁", "generic_name": "Persicae Semen",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-146", "name": "红花", "generic_name": "Carthami Flos",
     "category": "tcm-herb", "form": "饮片", "strength": "6g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-147", "name": "益母草", "generic_name": "Leonuri Herba",
     "category": "tcm-herb", "form": "饮片", "strength": "15g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-148", "name": "延胡索", "generic_name": "Corydalis Rhizoma",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-149", "name": "郁金", "generic_name": "Curcumae Radix",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
    {"id": "drug-150", "name": "枇杷叶", "generic_name": "Eriobotryae Folium",
     "category": "tcm-herb", "form": "饮片", "strength": "10g", "unit": "g",
     "hospital_dict": True, "demo_only": True},
]

# 2-3 hospital formulas (院内协定方) — instruct #51
DEMO_FORMULAS = [
    {"id": "fml-001", "name": "院内感冒方", "formula_type": "hospital",
     "indication": "风热感冒，发热咽痛", "department": "全科",
     "herbs": [{"name": "金银花", "dose": "15g"}, {"name": "连翘", "dose": "15g"},
               {"name": "板蓝根", "dose": "15g"}, {"name": "桔梗", "dose": "6g"},
               {"name": "甘草", "dose": "6g"}],
     "demo_only": True},
    {"id": "fml-002", "name": "院内健脾和胃方", "formula_type": "hospital",
     "indication": "脾胃虚弱，纳差便溏", "department": "中医科",
     "herbs": [{"name": "党参", "dose": "15g"}, {"name": "白术", "dose": "10g"},
               {"name": "茯苓", "dose": "15g"}, {"name": "陈皮", "dose": "6g"},
               {"name": "半夏", "dose": "9g"}, {"name": "甘草", "dose": "6g"}],
     "demo_only": True},
    {"id": "fml-003", "name": "院内安神方", "formula_type": "hospital",
     "indication": "心脾两虚，失眠多梦", "department": "中医科",
     "herbs": [{"name": "酸枣仁", "dose": "20g"}, {"name": "茯神", "dose": "15g"},
               {"name": "远志", "dose": "6g"}, {"name": "党参", "dose": "15g"},
               {"name": "当归", "dose": "10g"}, {"name": "甘草", "dose": "6g"}],
     "demo_only": True},
]


def _encounter(pid: str, doctor_id: str, chief: str, case_type: str, **kw) -> dict:
    return {
        "id": kw.get("eid", f"enc-{uuid.uuid4().hex[:8]}"),
        "patient_id": pid, "doctor_id": doctor_id,
        "chief_complaint": chief, "case_type": case_type,
        "status": "IN_PROGRESS", "demo_only": True,
        "ts": kw.get("ts", "2026-10-07T09:00:00+0800"),
        **{k: v for k, v in kw.items() if k not in ("eid", "ts")},
    }


def _patient(pid: str, name: str, sex: str, age: int, **kw) -> dict:
    return {
        "id": pid, "name": name, "sex": sex, "age": age,
        "demo_only": True, **kw,
    }


DEMO_PATIENTS = [
    _patient("p-001", "赵建国", "男", 45,
             phone="DEMO-138****1001", insurance="DEMO-INS-001",
             allergies=[{"allergen": "青霉素", "severity": "严重"}]),
    _patient("p-002", "孙美华", "女", 52,
             phone="DEMO-138****1002", insurance="DEMO-INS-002", allergies=[]),
    _patient("p-003", "周志强", "男", 61,
             phone="DEMO-138****1003", insurance="DEMO-INS-003",
             allergies=[{"allergen": "磺胺类", "severity": "中度"}]),
    _patient("p-004", "吴丽萍", "女", 38,
             phone="DEMO-138****1004", insurance="DEMO-INS-004", allergies=[]),
    _patient("p-005", "郑国华", "男", 68,
             phone="DEMO-138****1005", insurance="DEMO-INS-005",
             allergies=[{"allergen": "阿司匹林", "severity": "轻度"}]),
]

DEMO_ENCOUNTERS = [
    # case 1: respiratory
    _encounter("p-001", "dr-001", "发热3天伴咽痛咳嗽", "respiratory",
               eid="enc-001",
               history={"symptoms": ["发热", "咽痛", "咳嗽", "黄痰"],
                        "onset": "3天前", "temperature": "38.5℃"},
               labs=[{"item": "WBC", "value": "11.2", "unit": "10^9/L",
                      "ref": "4-10", "flag": "HIGH"},
                     {"item": "CRP", "value": "28", "unit": "mg/L",
                      "ref": "0-10", "flag": "HIGH"}],
               medications=[], raw_text="发热3天，咽痛咳嗽，黄痰，口渴，舌红苔薄黄，脉浮数。"),
    # case 2: digestive
    _encounter("p-002", "dr-002", "反复上腹胀痛2月，加重1周", "digestive",
               eid="enc-002",
               history={"symptoms": ["上腹胀痛", "反酸", "嗳气", "纳差"],
                        "onset": "反复2月"},
               labs=[{"item": "Hp", "value": "阳性", "unit": "", "ref": "阴性",
                      "flag": "HIGH"}],
               medications=[{"name": "奥美拉唑", "dose": "20mg", "freq": "qd"}],
               raw_text="反复上腹胀痛2月，反酸嗳气，纳差乏力，舌淡红苔白腻，脉弦细。"),
    # case 3: metabolic-chronic
    _encounter("p-003", "dr-003", "2型糖尿病复诊，血糖控制欠佳", "metabolic-chronic",
               eid="enc-003",
               history={"symptoms": ["口干多饮", "乏力", "下肢麻木"],
                        "comorbidities": ["2型糖尿病", "高血压"], "duration": "5年"},
               labs=[{"item": "HbA1c", "value": "8.2", "unit": "%",
                      "ref": "<7", "flag": "HIGH"},
                     {"item": "FBG", "value": "9.1", "unit": "mmol/L",
                      "ref": "3.9-6.1", "flag": "HIGH"}],
               medications=[{"name": "二甲双胍", "dose": "0.5g", "freq": "bid"}],
               raw_text="糖尿病5年，口干多饮乏力，下肢麻木，舌暗红苔白，脉沉细。"),
    # case 4: tcm-typical (失眠)
    _encounter("p-004", "dr-002", "失眠3月，入睡困难多梦", "tcm-typical",
               eid="enc-004",
               history={"symptoms": ["失眠", "多梦", "心悸", "健忘", "乏力"],
                        "onset": "3月前", "stress": "工作压力大"},
               labs=[],
               medications=[],
               raw_text="失眠3月，入睡困难，多梦易醒，伴心悸健忘乏力，面色少华，舌淡苔薄白，脉细弱。"),
    # case 5: high-risk-followup
    _encounter("p-005", "dr-001", "高血压急症后随访，头晕胸闷", "high-risk-followup",
               eid="enc-005",
               history={"symptoms": ["头晕", "胸闷", "视物模糊"],
                        "comorbidities": ["高血压3级", "冠心病"],
                        "recent_crisis": "1周前血压骤升至190/110mmHg"},
               labs=[{"item": "BP", "value": "165/100", "unit": "mmHg",
                      "ref": "<140/90", "flag": "HIGH"},
                     {"item": "cTnI", "value": "0.02", "unit": "ng/mL",
                      "ref": "<0.04", "flag": "NORMAL"}],
               medications=[{"name": "氨氯地平", "dose": "5mg", "freq": "qd"},
                            {"name": "氢氯噻嗪", "dose": "12.5mg", "freq": "qd"}],
               raw_text="高血压3级，1周前急症血压190/110，现头晕胸闷视物模糊，舌暗有瘀斑苔白腻，脉弦涩。"),
]


# -- knowledge corpus for RAG (instruct #74 metadata, instruct #21 evidence only) -----
DEMO_KNOWLEDGE = [
    {
        "title": "急性上呼吸道感染基层诊疗指南", "source_type": "guideline",
        "domain": "western", "subdomain": "respiratory", "trust_tier": "B",
        "version": "2024", "text":
        "## 急性上呼吸道感染\n急性上呼吸道感染多为病毒感染，表现为鼻塞、流涕、咽痛、咳嗽、发热。\n## 诊断要点\n发热、咽部充血、扁桃体肿大。血常规白细胞正常或偏低，细菌感染时升高。\n## 治疗原则\n以对症治疗为主。发热可用对乙酰氨基酚。细菌感染或症状重者可酌情使用阿莫西林。\n## 鉴别诊断\n需与流行性感冒、急性扁桃体炎、过敏性鼻炎鉴别。",
    },
    {
        "title": "慢性胃炎中西医诊治", "source_type": "guideline",
        "domain": "western", "subdomain": "digestive", "trust_tier": "B",
        "version": "2024", "text":
        "## 慢性胃炎\n慢性胃炎表现为上腹胀痛、反酸嗳气、纳差。Hp感染是重要病因。\n## 治疗\n抑酸：奥美拉唑；根除Hp：四联疗法；黏膜保护剂。\n## 中医辨证\n脾胃虚弱：四君子汤；肝胃不和：柴胡疏肝散；脾胃湿热：半夏泻心汤。",
    },
    {
        "title": "中国2型糖尿病防治指南", "source_type": "guideline",
        "domain": "western", "subdomain": "endocrine", "trust_tier": "A",
        "version": "2020", "text":
        "## 2型糖尿病\n诊断：空腹血糖≥7.0，或OGTT 2h≥11.1，或HbA1c≥6.5%。\n## 控制目标\nHbA1c<7%。老年/合并症者可放宽至<8%。\n## 治疗\n一线：二甲双胍。合并ASCVD加用SGLT2i/GLP-1RA。监测并发症：视网膜、肾病、神经病变。",
    },
    {
        "title": "中医不寐辨证论治", "source_type": "textbook",
        "domain": "tcm", "subdomain": "insomnia", "trust_tier": "B",
        "version": "第七版教材", "text":
        "## 不寐\n不寐以经常不能获得正常睡眠为特征。\n## 辨证分型\n心脾两虚：多梦易醒心悸健忘，面色少华，舌淡苔薄白，脉细弱。治法：补益心脾，养血安神。方药：归脾汤。\n肝火扰心：失眠急躁，口苦目赤，舌红苔黄，脉弦数。方药：龙胆泻肝汤。\n痰热内扰：失眠胸闷，苔黄腻，脉滑数。方药：温胆汤。",
    },
    {
        "title": "高血压急症处理与随访", "source_type": "guideline",
        "domain": "western", "subdomain": "cardiovascular", "trust_tier": "A",
        "version": "2023", "text":
        "## 高血压急症\n血压急剧升高(>180/120)伴靶器官损害。\n## 危险信号\n胸痛、呼吸困难、意识改变、视物模糊、剧烈头痛提示靶器官损害。\n## 随访要点\n急症后1周内密切随访。出现胸痛、呼吸困难、肢体无力、意识改变立即急诊。血压仍>160/100需调整方案。",
    },
    {
        "title": "中医胸痹与眩晕", "source_type": "textbook",
        "domain": "tcm", "subdomain": "cardiovascular", "trust_tier": "B",
        "version": "第七版教材", "text":
        "## 胸痹\n胸痹表现为发作性胸闷胸痛。气虚血瘀型：胸闷气短，舌暗有瘀斑，脉沉涩。治法：益气活血。方药：血府逐瘀汤合生脉散。\n## 眩晕\n肝阳上亢：眩晕耳鸣，腰膝酸软，舌红苔黄，脉弦。方药：天麻钩藤饮。瘀血阻络：眩晕头痛，舌暗有瘀斑，脉涩。方药：通窍活血汤。",
    },
]


async def seed_mock_his(his) -> dict:
    """Populate the in-memory MockHISAdapter with demo patients/encounters/drugs/formulas.
    Returns counts. Idempotent (skips already-seeded)."""
    if his._patients and his._drugs and his._formulas:
        return {"skipped": "already seeded",
                "patients": len(his._patients), "drugs": len(his._drugs),
                "formulas": len(his._formulas)}
    for p in DEMO_PATIENTS:
        his.seed_patient(p)
    for e in DEMO_ENCOUNTERS:
        his.seed_encounter(e)
    his.seed_drugs(DEMO_DRUGS)
    his.seed_formulas(DEMO_FORMULAS)
    return {"patients": len(DEMO_PATIENTS), "encounters": len(DEMO_ENCOUNTERS),
            "drugs": len(DEMO_DRUGS), "formulas": len(DEMO_FORMULAS)}


async def seed_knowledge() -> dict:
    """Chunk + embed + store the demo knowledge corpus into sqlite-vec."""
    from services.knowledge.rag import KnowledgeStore, chunk_text, metadata_for
    from packages.shared.providers import EmbeddingProvider, AIUnavailable

    store = KnowledgeStore()
    total_chunks = 0
    chunk_dicts: list[dict] = []
    for doc in DEMO_KNOWLEDGE:
        chunks = chunk_text(doc["text"])
        for i, c in enumerate(chunks):
            chunk_dicts.append({
                "document_id": "", "source_id": doc["title"],
                "text": c, "chunk_index": i,
                "metadata": metadata_for(
                    source_type=doc["source_type"], domain=doc["domain"],
                    subdomain=doc.get("subdomain", ""), title=doc["title"],
                    trust_tier=doc.get("trust_tier", "E"),
                    version=doc.get("version", "")),
            })
    if not chunk_dicts:
        return {"chunks": 0}
    try:
        emb = EmbeddingProvider("embedding")
        vecs = await emb.embed([c["text"] for c in chunk_dicts])
    except AIUnavailable as e:
        # instruct #30: knowledge seeding failure must not block API startup
        print(f"[seed] embedding unavailable; knowledge not seeded: {e}", flush=True)
        return {"chunks": len(chunk_dicts), "seeded": 0, "error": str(e)}
    ids = await store.add_chunks(chunk_dicts, vecs)
    return {"chunks": len(ids), "documents": len(DEMO_KNOWLEDGE)}


async def seed_all(his) -> dict:
    """Entry point called from API lifespan in dev mode.

    Delegates to the idempotent init_demo.init_demo_data (demo.md 第四节):
    upserts golden-path entities into ORM tables + MockHIS memory, skips
    already-seeded records, and never overwrites doctor-edited medical records.
    Kept for backward-compat with any caller that imports seed_all."""
    from scripts.init_demo import init_demo_data
    return await init_demo_data(his)


def main():
    """Standalone run: seeds knowledge into sqlite (mock-HIS is process-local
    and can only be seeded in-process, so for mock data use the /health+endpoints
    of the running API which seeds on startup)."""
    import asyncio
    from packages.shared.db import init_engine
    from packages.shared.his_adapter import get_his_adapter

    async def _run():
        await init_engine()
        his = get_his_adapter()
        r = await seed_all(his)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    asyncio.run(_run())


if __name__ == "__main__":
    main()
