"""内部属性代码与正文脱敏边界；手动运行，不调用真实服务。"""
import unittest
from test_memory import WHEN
from core.providers.memory.mem_local_short.facts import validate_fact, redact_tree


class MetadataTests(unittest.TestCase):
    def candidate(self):
        return dict(subject="user",predicate="饮食偏好",value="喜欢西红柿",
                    evidence="我喜欢西红柿",temporalScope="current",entityId="user",
                    predicateId="p_example",canonicalPredicate="attr_13812345678abcde",
                    predicateDefinition="对该食物明确表达的态度")

    def test_generated_hash_is_not_treated_as_phone_number(self):
        for code in ("attr_13812345678abcde","attr_13812345678abcde13812345678abcde"):
            with self.subTest(code=code):
                raw={**self.candidate(),"canonicalPredicate":code}
                result=validate_fact(raw,"我喜欢西红柿",WHEN.date())
                self.assertEqual(code,result["canonicalPredicate"])
                self.assertEqual(code,redact_tree(result)["canonicalPredicate"])

    def test_same_digits_in_fact_value_are_still_rejected(self):
        raw={**self.candidate(),"value":"13812345678","evidence":"号码13812345678"}
        with self.assertRaisesRegex(ValueError,"sensitive_value"):
            validate_fact(raw,raw["evidence"],WHEN.date())

    def test_error_identifies_invalid_metadata_field(self):
        raw={**self.candidate(),"predicateDefinition":None}
        with self.assertRaisesRegex(ValueError,"canonical_metadata_invalid:predicateDefinition:empty_or_type"):
            validate_fact(raw,"我喜欢西红柿",WHEN.date())

    def test_non_hash_code_does_not_gain_exemption(self):
        raw={**self.candidate(),"canonicalPredicate":"phone_13812345678"}
        with self.assertRaisesRegex(ValueError,"canonical_metadata_invalid:canonicalPredicate:sensitive"):
            validate_fact(raw,"我喜欢西红柿",WHEN.date())


if __name__=="__main__": unittest.main()
