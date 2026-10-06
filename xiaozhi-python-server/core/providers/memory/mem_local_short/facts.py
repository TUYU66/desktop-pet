"""对象、属性、值和时间相互独立。校验结构，不用领域关键词猜事实。"""
import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

CATEGORIES = {"profile","preference","relationship","event","goal","habit","note"}
CHECKS = ("sourceSupported","subjectSupported","attributeSupported","temporalSupported","transitionSupported","atomic","longTerm")
LABEL = r"(?:密码|口令|验证码|密钥|令牌|身份证(?:号)?|银行卡(?:号)?|信用卡(?:号)?|api\s*key|access[_\s-]*token|token|password|secret)"
SECRET = re.compile(LABEL+r"\s*(?:是|为|[:：=])\s*(?:\"[^\"]*\"|'[^']*'|[^\s，。；,;]+)",re.I)
TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b|(?<!\d)(?:\d{16,19}|\d{17}[\dXx]|1[3-9]\d{9})(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

def redact(text):
    return TOKEN.sub("[REDACTED]",SECRET.sub("[REDACTED]",str(text or "")))

def generated_predicate_code(value):
    """仅识别程序生成的属性哈希代码，不用于豁免事实正文或任意英文代码。"""
    return isinstance(value,str) and re.fullmatch(r"(?:completed_)?attr_(?:[a-f0-9]{16}|[a-f0-9]{32})",value) is not None

def redact_tree(value):
    """逐个字符串脱敏，不能对 JSON 序列化文本替换后再解析。"""
    if isinstance(value, str): return redact(value)
    if isinstance(value, dict): return {key: item if key=="canonicalPredicate" and generated_predicate_code(item)
                                       else redact_tree(item) for key, item in value.items()}
    if isinstance(value, list): return [redact_tree(item) for item in value]
    return value

def normalize(text):
    return re.sub(r"[\W_]+","",unicodedata.normalize("NFKC",str(text)).lower())

def digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()

def fact_key(fact):
    return digest(entity_id(fact)+"\x1f"+predicate_id(fact))

def entity_id(fact):
    return fact.get("entityId") or ("user" if fact["subject"]=="user" else "e_"+digest(normalize(fact["subject"]))[:40])

def predicate_id(fact):
    return fact.get("predicateId") or "p_"+digest(normalize(fact["predicate"]))[:40]

def same_slot(left,right):
    return fact_key(left)==fact_key(right) and left["temporalScope"]==right["temporalScope"] and (left["temporalScope"]=="current" or (left["timePrecision"],left["timeValue"])==(right["timePrecision"],right["timeValue"]))

def same_value(left,right):
    a,b=numeric_quantity(left),numeric_quantity(right)
    return a==b if a is not None and b is not None else normalize(left)==normalize(right)

def numeric_quantity(value):
    """识别简单数量的数值与单位，供通用算术校验；不识别业务对象。"""
    match=re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)\s*([^\d\s]{0,12})\s*",value)
    if not match: return None
    number,unit=match.groups()
    # 中文数词不能混入单位；“三只猫两只狗”不是3个“只猫两只狗”。
    if re.search(r"[零〇一二两三四五六七八九十]",unit): return None
    digits={char:i for i,char in enumerate("零一二三四五六七八九")}; digits.update({"〇":0,"两":2})
    try:
        if "十" in number:
            left,right=number.split("十")
            amount=Decimal((digits[left] if left else 1)*10+(digits[right] if right else 0))
        elif number in digits: amount=Decimal(digits[number])
        else: amount=Decimal(number)
        return amount,unit
    except (ValueError,KeyError,InvalidOperation): return None

def check_numeric_transition(source,old,new):
    """模型不能把变化量当最终量；能解析的数字在本地核对，不信任自报结论。"""
    before,after=numeric_quantity(old["value"]),numeric_quantity(new["value"])
    if before is None or after is None:
        if source["intent"]=="relative": raise ValueError("numeric_transition_unresolved")
        return
    if before[1]!=after[1]: raise ValueError("numeric_unit_changed")
    if source["intent"]=="relative":
        delta=source.get("delta")
        if not isinstance(delta,(int,float)) or isinstance(delta,bool): raise ValueError("numeric_delta_missing")
        amount=Decimal(str(delta))
        if not amount.is_finite() or before[0]+amount!=after[0]: raise ValueError("numeric_delta_inconsistent")
    else:
        stated=numeric_quantity(source["fact"]["value"])
        if stated is None or stated!=after: raise ValueError("numeric_absolute_value_unsupported")

def numeric_mentions(text):
    """只提供原话确实出现的数字，不从日期/身份推算新值。"""
    values=[]
    for token in re.findall(r"[+-]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+",text):
        parsed=numeric_quantity(token)
        if parsed is not None: values.append(parsed[0])
    return values

def evidence_matches(evidence,text):
    # 证据与身份归一不是一回事：不能删掉标点把“2，1”当成“21”。
    return bool(normalize(evidence)) and evidence in text

def validate_fact(raw,text,observed):
    if not isinstance(raw,dict): raise ValueError("fact_not_object")
    fact = {}
    for key,cap in (("subject",80),("predicate",80),("value",500)):
        value = raw.get(key)
        if not isinstance(value,str) or not normalize(value) or len(value)>cap: raise ValueError("invalid_"+key)
        if redact(value)!=value or "[REDACTED]" in value: raise ValueError("sensitive_"+key)
        fact[key]=value.strip()
    evidence=raw.get("evidence",raw.get("sourceEvidence",""))
    if not isinstance(evidence,str) or len(evidence)>600 or not evidence_matches(evidence,text) or "[REDACTED]" in evidence: raise ValueError("evidence_not_supported")
    fact.update(sourceEvidence=evidence,observedAt=observed.isoformat())
    if "memoryMode" in raw or "memoryField" in raw:
        from .policy import CORE_FIELDS
        from .categories import AUTO_CATEGORIES
        mode,field=raw.get("memoryMode"),raw.get("memoryField")
        if not (mode=="core" and field in CORE_FIELDS or mode=="automatic" and field in AUTO_CATEGORIES or mode=="explicit" and field=="custom"):
            raise ValueError("memory_policy_metadata_invalid")
        fact.update(memoryMode=mode,memoryField=field)
    if "entityId" in raw or "predicateId" in raw:
        for key in ("entityId","predicateId"):
            value=raw.get(key)
            if not isinstance(value,str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}",value): raise ValueError("canonical_id_invalid")
            fact[key]=value
        for key,cap in (("canonicalPredicate",48),("predicateDefinition",160),("contextEvidence",600),("contextMessageId",64)):
            if key in raw:
                value=raw[key]
                if not isinstance(value,str) or not value.strip(): raise ValueError("canonical_metadata_invalid:"+key+":empty_or_type")
                if len(value)>cap: raise ValueError("canonical_metadata_invalid:"+key+":too_long")
                if not (key=="canonicalPredicate" and generated_predicate_code(value)) and redact(value)!=value:
                    raise ValueError("canonical_metadata_invalid:"+key+":sensitive")
                if key=="canonicalPredicate" and not re.fullmatch(r"[a-z][a-z0-9_]{1,47}",value): raise ValueError("canonical_metadata_invalid:"+key+":format")
                fact[key]=value
        for key in ("entityAliases","predicateAliases"):
            values=raw.get(key,[])
            if not isinstance(values,list) or len(values)>8 or any(not isinstance(v,str) or not normalize(v) or len(v)>80 or redact(v)!=v for v in values): raise ValueError("canonical_alias_invalid")
            fact[key]=values
    scope=raw.get("temporalScope")
    if scope not in {"current","historical","future"}: raise ValueError("invalid_temporal_scope")
    fact["temporalScope"]=scope
    if scope=="current":
        fact.update(timePrecision="day",timeValue=observed.isoformat())
        return fact
    precision,value=raw.get("timePrecision"),raw.get("timeValue")
    if not isinstance(value,str) or not value.strip() or len(value)>80: raise ValueError("invalid_time_value")
    if precision=="year" and re.fullmatch(r"\d{4}",value):
        first=date(int(value),1,1); after=first.year>observed.year; before=first.year<observed.year
    elif precision=="month" and re.fullmatch(r"\d{4}-\d{2}",value):
        first=date.fromisoformat(value+"-01"); after=first>observed; before=first<observed.replace(day=1)
    elif precision=="day":
        first=date.fromisoformat(value); after=first>observed; before=first<observed
    elif precision=="approximate" and scope in {"historical","future"}:
        if re.search(r"去年|前年|今年|明年|昨天|今天|上周|下周|上个月|下个月",value): raise ValueError("unresolved_calendar_time")
        after=before=False
    else: raise ValueError("invalid_time_precision")
    if scope=="historical" and after or scope=="future" and before: raise ValueError("time_outside_scope")
    fact.update(timePrecision=precision,timeValue=value.strip())
    return fact

def render_fact(fact):
    subject="用户" if fact["subject"]=="user" else fact["subject"]
    time=fact["timeValue"]+("年" if fact["timePrecision"]=="year" else "")
    qualifier={"current":"截至","historical":"历史时间：","future":"计划时间："}[fact["temporalScope"]]+time
    return f'{subject}的{fact["predicate"]}：{fact["value"]}（{qualifier}）'

def decode_fact(memory):
    try:
        fact=json.loads(memory.get("factJson") or "null")
        return fact if isinstance(fact,dict) else None
    except (TypeError,ValueError): return None

def audit_approved(payload):
    return isinstance(payload,dict) and all(payload.get(key) is True for key in CHECKS)

def prompt_memory(memory):
    fact=decode_fact(memory)
    if fact and isinstance(fact.get("stateHistory"),list):
        fact={**fact,"stateHistory":fact["stateHistory"][-3:],"earlierHistoryCount":max(0,len(fact["stateHistory"])-3)}
    return {"id":memory["id"],"version":memory.get("version",0),"category":memory.get("category","note"),
            "fact":redact_tree(fact),
            "legacyPreview":redact(memory.get("content",""))[:400] if not fact else None}

def query_terms(text):
    words=re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}",redact(text))
    terms=[]
    for word in words:
        terms.append(word[:60])
        if re.fullmatch(r"[\u4e00-\u9fff]+",word):
            # 仅用于扩大检索召回，不用文本重叠批准新增、更新或删除。
            terms.extend(word[i:i+2] for i in range(len(word)-1))
    return list(dict.fromkeys(terms))[:12]
