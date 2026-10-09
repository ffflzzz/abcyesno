"""Exercise the compiled role boundary without model/network calls."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from langchain_core.messages import HumanMessage,AIMessage
from v5 import orchestrator

class TestRoleTaskInput(unittest.IsolatedAsyncioTestCase):
    async def _run(self,state):
        captured=[]
        class FakeAgent:
            async def ainvoke(self,value,config):
                captured.append(value['messages'][0].content)
                return {'messages':[AIMessage(content='done')]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(orchestrator,'_root',Path(tmp)),patch.object(orchestrator,'_EP',1),patch.object(orchestrator,'role_chat',return_value=object()),patch.object(orchestrator,'create_agent',return_value=FakeAgent()),patch.object(orchestrator,'load_manifest',return_value={}),patch.object(orchestrator,'record_phase'):
                graph=orchestrator._build_role_graph('worldbuilder','shortdrama')
                await graph.ainvoke(state)
        return captured[0]
    async def test_explicit_brief_survives_compiled_state_schema(self):
        task='修订：保留右手持玉，第二段从门外行进接起。'
        self.assertIn(task,await self._run({'brief':task}))
    async def test_native_subagent_message_reaches_role(self):
        task='返工：第一段12秒，第二段12秒，不能重复交接。'
        result=await self._run({'messages':[HumanMessage(content=task),AIMessage(content='previous result')]})
        self.assertIn(task,result)
        self.assertNotIn('previous result',result)
    async def test_explicit_brief_precedes_old_message(self):
        result=await self._run({'brief':'当前修订意见','messages':[HumanMessage(content='旧任务内容')]})
        self.assertIn('当前修订意见',result)
        self.assertNotIn('旧任务内容',result)
    async def test_serialized_agent_protocol_message(self):
        result=await self._run({'messages':[{'role':'user','content':[{'type':'text','text':'异步返工意见必须传入角色'}]}]})
        self.assertIn('异步返工意见必须传入角色',result)

class TestRoleDelivery(unittest.IsolatedAsyncioTestCase):
    async def _run(self, artifact, ep=1):
        class FakeAgent:
            async def ainvoke(self,value,config):
                return {'messages':[AIMessage(content='已经交付，可以继续',
                    response_metadata={'finish_reason':'stop'})]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            if artifact is not None:
                target=root/orchestrator.out_path('scriptwriter',ep)
                target.parent.mkdir(parents=True,exist_ok=True)
                if isinstance(artifact,bytes):
                    target.write_bytes(artifact)
                else:
                    target.write_text(artifact,encoding='utf-8')
            with patch.object(orchestrator,'_root',root),patch.object(orchestrator,'_EP',1),patch.object(orchestrator,'role_chat',return_value=object()),patch.object(orchestrator,'create_agent',return_value=FakeAgent()),patch.object(orchestrator,'load_manifest',return_value={'episode_index':2}),patch.object(orchestrator,'record_phase'):
                result=await orchestrator._build_role_graph('scriptwriter','shortdrama').ainvoke({'brief':'写本集剧本'})
                return result['messages'][-1].content

    async def test_missing_file_overrides_model_success(self):
        self.assertIn('【未交付】',await self._run(None))

    async def test_previous_episode_cannot_satisfy_current_delivery(self):
        self.assertIn('第2集',await self._run('上一集已有完整剧本',ep=1))
        self.assertIn('【未交付】',await self._run(' \n',ep=2))

    async def test_current_nonempty_file_preserves_reply(self):
        self.assertEqual('已经交付，可以继续',await self._run('当前集剧本',ep=2))

    async def test_corrupt_text_returns_failure_instead_of_crashing(self):
        self.assertIn('【未交付】',await self._run(b'valid prefix\xff',ep=2))

if __name__=='__main__':unittest.main()
