"""The skills taxonomy: names, aliases, and how skills imply or relate to one another.

Matching is by alias. ``implies`` means holding one skill is reasonable evidence for the other (Kubernetes
suggests container experience). ``related`` means the skills are neighbours (PostgreSQL and MySQL). The built-in
list is a starting point for technology roles. Extend or replace it with your own YAML file.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import yaml
from pydantic import ValidationError

from jobmatch.errors import ConfigurationError
from jobmatch.models import Skill

MAX_FILE_BYTES = 2_000_000


def _s(
    sid: str,
    name: str,
    category: str,
    aliases: tuple[str, ...] = (),
    implies: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    *,
    cs: bool = False,
) -> Skill:
    return Skill(
        id=sid,
        name=name,
        category=category,
        aliases=aliases,
        implies=implies,
        related=related,
        case_sensitive=cs,
    )


_BUILTIN: tuple[Skill, ...] = (
    _s("python", "Python", "language", ("python3",), related=("java", "go")),
    _s("java", "Java", "language", (), related=("kotlin", "csharp")),
    _s("javascript", "JavaScript", "language", ("js", "ecmascript"), related=("typescript",)),
    _s("typescript", "TypeScript", "language", ("ts",), implies=("javascript",)),
    _s("go", "Go", "language", ("golang",), cs=True),
    _s("rust", "Rust", "language", ()),
    _s("csharp", "C#", "language", ("c#", "c sharp"), related=("java",)),
    _s("cpp", "C++", "language", ("c++", "cpp")),
    _s("kotlin", "Kotlin", "language", (), implies=("java",)),
    _s("swift", "Swift", "language", ()),
    _s("ruby", "Ruby", "language", ()),
    _s("php", "PHP", "language", ()),
    _s("scala", "Scala", "language", (), related=("java",)),
    _s("sql", "SQL", "data", ("t-sql", "pl/sql", "structured query language")),
    _s("bash", "Bash", "language", ("shell scripting", "shell script", "bash scripting")),
    _s(
        "react",
        "React",
        "framework",
        ("reactjs", "react.js"),
        implies=("javascript",),
        related=("vue", "angular"),
    ),
    _s(
        "vue",
        "Vue",
        "framework",
        ("vuejs", "vue.js"),
        implies=("javascript",),
        related=("react", "angular"),
    ),
    _s(
        "angular",
        "Angular",
        "framework",
        ("angularjs",),
        implies=("typescript",),
        related=("react", "vue"),
    ),
    _s("nodejs", "Node.js", "framework", ("node", "nodejs", "node.js"), implies=("javascript",)),
    _s("django", "Django", "framework", (), implies=("python",), related=("flask", "fastapi")),
    _s("flask", "Flask", "framework", (), implies=("python",), related=("django", "fastapi")),
    _s("fastapi", "FastAPI", "framework", (), implies=("python",), related=("flask", "django")),
    _s("spring", "Spring", "framework", ("spring boot", "springboot"), implies=("java",)),
    _s("dotnet", ".NET", "framework", (".net", "dotnet", "asp.net"), implies=("csharp",)),
    _s("rails", "Ruby on Rails", "framework", ("ruby on rails", "rails"), implies=("ruby",)),
    _s(
        "postgresql",
        "PostgreSQL",
        "data",
        ("postgres", "postgresql"),
        implies=("sql",),
        related=("mysql",),
    ),
    _s("mysql", "MySQL", "data", ("mariadb",), implies=("sql",), related=("postgresql",)),
    _s("mongodb", "MongoDB", "data", ("mongo",), related=("dynamodb",)),
    _s("redis", "Redis", "data", ()),
    _s("dynamodb", "DynamoDB", "data", (), related=("mongodb",)),
    _s("elasticsearch", "Elasticsearch", "data", ("opensearch",)),
    _s("kafka", "Kafka", "data", ("apache kafka",), related=("rabbitmq",)),
    _s("rabbitmq", "RabbitMQ", "data", (), related=("kafka",)),
    _s("spark", "Apache Spark", "data", ("spark", "pyspark"), related=("hadoop",)),
    _s("hadoop", "Hadoop", "data", (), related=("spark",)),
    _s("airflow", "Airflow", "data", ("apache airflow",)),
    _s("dbt", "dbt", "data", ()),
    _s("snowflake", "Snowflake", "data", (), implies=("sql",), related=("bigquery", "redshift")),
    _s("bigquery", "BigQuery", "data", (), implies=("sql",), related=("snowflake", "redshift")),
    _s("redshift", "Redshift", "data", (), implies=("sql",), related=("snowflake", "bigquery")),
    _s(
        "aws",
        "AWS",
        "cloud",
        ("amazon web services", "ec2", "s3", "lambda"),
        implies=("cloud",),
        related=("azure", "gcp"),
    ),
    _s("azure", "Azure", "cloud", ("microsoft azure",), implies=("cloud",), related=("aws", "gcp")),
    _s(
        "gcp",
        "Google Cloud",
        "cloud",
        ("google cloud", "google cloud platform", "gcp"),
        implies=("cloud",),
        related=("aws", "azure"),
    ),
    _s("cloud", "Cloud computing", "cloud", ("cloud computing", "public cloud")),
    _s("docker", "Docker", "devops", (), implies=("containers",)),
    _s(
        "containers", "Containers", "devops", ("containerization", "containerisation", "containers")
    ),
    _s(
        "kubernetes",
        "Kubernetes",
        "devops",
        ("k8s", "eks", "gke", "aks"),
        implies=("containers", "docker"),
    ),
    _s(
        "terraform",
        "Terraform",
        "devops",
        (),
        implies=("iac",),
        related=("cloudformation", "pulumi"),
    ),
    _s("cloudformation", "CloudFormation", "devops", (), implies=("iac",), related=("terraform",)),
    _s("pulumi", "Pulumi", "devops", (), implies=("iac",), related=("terraform",)),
    _s(
        "iac",
        "Infrastructure as code",
        "devops",
        ("infrastructure as code", "infrastructure-as-code"),
    ),
    _s("ansible", "Ansible", "devops", (), implies=("iac",)),
    _s(
        "cicd",
        "CI/CD",
        "devops",
        (
            "ci/cd",
            "continuous integration",
            "continuous delivery",
            "continuous deployment",
            "github actions",
            "jenkins",
            "gitlab ci",
            "circleci",
        ),
    ),
    _s("git", "Git", "devops", ("github", "gitlab", "version control")),
    _s("linux", "Linux", "devops", ("unix", "ubuntu", "red hat")),
    _s("prometheus", "Prometheus", "devops", (), related=("grafana",)),
    _s("grafana", "Grafana", "devops", (), related=("prometheus",)),
    _s("observability", "Observability", "devops", ("monitoring", "opentelemetry", "datadog")),
    _s(
        "ml",
        "Machine learning",
        "ml",
        ("machine learning", "ml"),
        related=("deep-learning", "statistics"),
    ),
    _s(
        "deep-learning",
        "Deep learning",
        "ml",
        ("deep learning", "neural networks"),
        implies=("ml",),
    ),
    _s(
        "pytorch", "PyTorch", "ml", (), implies=("deep-learning", "python"), related=("tensorflow",)
    ),
    _s(
        "tensorflow",
        "TensorFlow",
        "ml",
        ("keras",),
        implies=("deep-learning", "python"),
        related=("pytorch",),
    ),
    _s("scikit-learn", "scikit-learn", "ml", ("sklearn", "scikit learn"), implies=("ml", "python")),
    _s(
        "nlp",
        "Natural language processing",
        "ml",
        ("natural language processing", "nlp", "text mining"),
        implies=("ml",),
    ),
    _s(
        "llm",
        "Large language models",
        "ml",
        ("llm", "llms", "large language models", "generative ai", "genai", "prompt engineering"),
        implies=("nlp",),
    ),
    _s(
        "rag",
        "Retrieval-augmented generation",
        "ml",
        ("retrieval-augmented generation", "retrieval augmented generation", "rag"),
        implies=("llm",),
    ),
    _s(
        "mlops",
        "MLOps",
        "ml",
        ("ml ops", "model deployment", "mlflow", "kubeflow"),
        implies=("ml",),
    ),
    _s(
        "statistics",
        "Statistics",
        "ml",
        ("statistical modeling", "statistical modelling", "hypothesis testing", "a/b testing"),
    ),
    _s("pandas", "pandas", "ml", (), implies=("python",)),
    _s("computer-vision", "Computer vision", "ml", ("computer vision",), implies=("ml",)),
    _s(
        "security",
        "Application security",
        "security",
        ("appsec", "application security", "secure coding", "owasp"),
    ),
    _s(
        "iam",
        "Identity and access management",
        "security",
        ("identity and access management", "iam", "oauth", "oidc", "sso"),
    ),
    _s("threat-modeling", "Threat modeling", "security", ("threat modeling", "threat modelling")),
    _s(
        "pentest",
        "Penetration testing",
        "security",
        ("penetration testing", "pentesting", "red team"),
    ),
    _s("siem", "SIEM", "security", ("splunk", "sentinel", "siem")),
    _s(
        "compliance",
        "Compliance",
        "security",
        ("soc 2", "soc2", "iso 27001", "hipaa", "pci dss", "gdpr"),
    ),
    _s(
        "rest",
        "REST APIs",
        "engineering",
        ("rest api", "rest apis", "restful", "restful apis", "rest"),
    ),
    _s("graphql", "GraphQL", "engineering", ()),
    _s("grpc", "gRPC", "engineering", ("grpc", "protobuf", "protocol buffers")),
    _s(
        "microservices",
        "Microservices",
        "engineering",
        ("microservice", "service-oriented architecture"),
    ),
    _s(
        "system-design",
        "System design",
        "engineering",
        ("system design", "distributed systems", "software architecture", "scalable systems"),
    ),
    _s(
        "testing",
        "Automated testing",
        "engineering",
        (
            "unit testing",
            "integration testing",
            "test automation",
            "pytest",
            "junit",
            "tdd",
            "test-driven development",
        ),
    ),
    _s("agile", "Agile", "practice", ("scrum", "kanban", "agile methodologies")),
    _s(
        "leadership",
        "Technical leadership",
        "practice",
        (
            "team lead",
            "tech lead",
            "technical leadership",
            "mentoring",
            "mentored",
            "people management",
            "led a team",
        ),
    ),
    _s(
        "communication",
        "Communication",
        "practice",
        ("stakeholder management", "cross-functional", "technical writing", "presentations"),
    ),
    _s(
        "product",
        "Product management",
        "practice",
        ("product management", "roadmap", "product strategy", "user research"),
    ),
    _s(
        "data-viz",
        "Data visualization",
        "data",
        ("tableau", "power bi", "looker", "data visualization", "data visualisation"),
    ),
    _s("excel", "Excel", "data", ("microsoft excel", "spreadsheets")),
    _s("sales", "Sales", "business", ("account management", "business development", "quota")),
    _s(
        "finance",
        "Financial analysis",
        "business",
        ("financial modeling", "financial modelling", "forecasting", "budgeting"),
    ),
)


class Taxonomy:
    """Skill lookup by id, plus mention detection by alias."""

    def __init__(self, skills: tuple[Skill, ...] = _BUILTIN) -> None:
        ids = [s.id for s in skills]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("skill ids must be unique")
        self._skills = {s.id: s for s in skills}
        known = set(ids)
        for skill in skills:
            for other in (*skill.implies, *skill.related):
                if other not in known:
                    raise ConfigurationError(f"skill {skill.id} refers to unknown skill {other!r}")
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for skill in skills:
            names = {skill.name, *skill.aliases}
            if not skill.case_sensitive:
                names.add(skill.id)
            flags = 0 if skill.case_sensitive else re.IGNORECASE
            alternation = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
            self._patterns.append(
                (
                    skill.id,
                    re.compile(rf"(?<![A-Za-z0-9+#.])(?:{alternation})(?![A-Za-z0-9+#])", flags),
                )
            )

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def mentions(self, text: str) -> list[str]:
        """Skill ids mentioned in ``text``, in taxonomy order, ignoring negated mentions."""
        found: list[str] = []
        for skill_id, pattern in self._patterns:
            for match in pattern.finditer(text):
                if not _negated(text, match.start()):
                    found.append(skill_id)
                    break
        return found

    def resolve(self, text: str) -> str | None:
        """The skill id for a name or alias such as ``k8s``, or ``None``."""
        cleaned = text.strip()
        for skill_id, pattern in self._patterns:
            match = pattern.fullmatch(cleaned)
            if match:
                return skill_id
        return None

    def search(self, query: str) -> list[Skill]:
        needle = query.strip().lower()
        return [
            s
            for s in self._skills.values()
            if needle in s.id or needle in s.name.lower() or any(needle in a for a in s.aliases)
        ]


_NEGATION = re.compile(
    r"(?i)\b(?:no|not|never|without|lack(?:s|ing)?|unfamiliar with|limited|zero)\b[^.;\n]{0,40}$"
)


def _negated(text: str, start: int) -> bool:
    return bool(_NEGATION.search(text[max(0, start - 45) : start]))


def load_taxonomy(path: Path | None) -> Taxonomy:
    """The built-in taxonomy, with the skills in ``path`` added or replaced.

    Raises:
        ConfigurationError: If the file is unreadable or a skill is invalid.
    """
    if path is None:
        return Taxonomy()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ConfigurationError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read {path.name}: {exc}") from exc
    entries = data.get("skills") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ConfigurationError(f"{path.name} must contain a list of skills")
    merged = {s.id: s for s in _BUILTIN}
    try:
        for raw in entries:
            skill = Skill.model_validate(raw)
            merged[skill.id] = skill
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ConfigurationError(f"invalid skill in {path.name} ({where}: {first['msg']})") from exc
    return Taxonomy(tuple(merged.values()))
