"""Public BRNESTRM authentication adapter: DENY ALL by design."""
class CandidateAuthAuthority:
    @classmethod
    def get_gateway_singleton(cls,*args,**kwargs): return cls()
    def verify_request_capability(self,**kwargs):
        return False,403,"public_auth_not_configured",None
    def authenticate_operator_and_bootstrap_scope(self,**kwargs):
        return False,403,"public_auth_not_configured",None
    def step_up(self,**kwargs):
        return False,403,"public_auth_not_configured"
    def revoke_token(self,*args,**kwargs):
        raise RuntimeError("public_auth_not_configured")
