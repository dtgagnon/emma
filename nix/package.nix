{
  lib,
  python313,
}:

python313.pkgs.buildPythonApplication {
  pname = "emma";
  version = "0.1.0";
  pyproject = true;

  src = ./..;

  build-system = [ python313.pkgs.hatchling ];

  dependencies = with python313.pkgs; [
    # LLM integrations
    anthropic
    ollama

    # CLI and config
    typer
    rich
    pyyaml
    pydantic
    pydantic-settings

    # Email handling
    imapclient
    aiosmtplib

    # Async and scheduling
    aiofiles
    apscheduler

    # Database
    sqlalchemy
    aiosqlite
  ];

  # Skip tests during build (run separately)
  doCheck = false;

  pythonImportsCheck = [ "email_agent" ];

  meta = with lib; {
    description = "Email automation platform with LLM processing, rules engine, and integrations";
    homepage = "https://github.com/dtgagnon/emma";
    license = licenses.mit;
    maintainers = [ ];
    mainProgram = "emma";
  };
}
