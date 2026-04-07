#!/usr/bin/env bash
# shellcheck disable=SC2034  # SC variables are consumed by the scriptcast tracer
: SC helpers
: SC set width 80
: SC set height 14
: SC set type_speed 60
: SC set cmd_wait 800
: SC set exit_wait 800
: SC set input_wait 300


# ---------------------------------------------------------------------------
# Scene 1: Open a project
# ---------------------------------------------------------------------------
: SC scene "Open a project"

: SC \\ ${YELLOW}Create a new template${RESET}
: SC mock devcode new dev <<'EOF'
Created template 'dev' at ~/.local/share/dev-code/templates/dev
EOF

: SC \\ ${YELLOW}Open a project in VS Code using the template${RESET}
: SC mock devcode open dev ~/projects/my-app <<EOF
${GREEN} Opening ~/projects/my-app in VS Code...${RESET}
EOF

# ---------------------------------------------------------------------------
# Scene 2: Manage templates
# ---------------------------------------------------------------------------
: SC scene "Manage templates"

: SC \\ ${YELLOW}List existing templates${RESET}
: SC mock devcode list <<'EOF'
dev
EOF

: SC \\ ${YELLOW}Create another template${RESET}
: SC mock devcode new demo <<'EOF'
Created template 'demo' at ~/.local/share/dev-code/templates/demo
EOF

: SC \\ ${YELLOW}List templates again to confirm${RESET}
: SC mock devcode list <<'EOF'
dev
demo
EOF

: SC \\ ${YELLOW}Edit the template in VS Code${RESET}
: SC mock devcode edit demo <<EOF
${GREEN} Opening 'demo' template in VS Code...${RESET}
EOF

# ---------------------------------------------------------------------------
# Scene 3: Container status
# ---------------------------------------------------------------------------
: SC scene "Container status"

: SC \\ ${YELLOW}Show running containers${RESET}
: SC mock devcode ps <<'EOF'
#  CONTAINER ID  TEMPLATE  PROJECT PATH                 STATUS
1  a1b9afa16218  dev       ~/projects/my-app            Up 3 min
EOF

: SC \\ ${YELLOW}Show all containers including stopped ones${RESET}
: SC mock devcode ps -a <<'EOF'
#  CONTAINER ID  TEMPLATE  PROJECT PATH                 STATUS
1  a1b2c3d4e5f6  claude    ~/projects/mk3serve          Exited (0) 2 weeks ago
2  9f8e7d6c5b4a  py-dev    ~/projects/py-app            Exited (0) 1 hours ago
3  a1b9afa16218  dev       ~/projects/my-app            Up 3 min
EOF

: SC \\ ${YELLOW}Interactively pick a container to reopen${RESET}
: SC mock devcode ps -a -i <<EOF
#  CONTAINER ID  TEMPLATE  PROJECT PATH                 STATUS
1  a1b2c3d4e5f6  claude    ~/projects/mk3serve          Exited (0) 2 weeks ago
2  9f8e7d6c5b4a  py-dev    ~/projects/py-app            Exited (0) 1 hours ago
3  a1b9afa16218  dev       ~/projects/my-app            Up 3 min

Open [1-3]: 2
${GREEN} Opening '~/projects/py-app' container in VS Code...${RESET}
EOF
