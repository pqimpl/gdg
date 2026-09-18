#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TOTAL_LEVELS 3

/* ANSI terminal colours */
#define RESET   "\033[0m"
#define RED     "\033[31m"
#define GREEN   "\033[32m"
#define CYAN    "\033[36m"
#define YELLOW  "\033[33m"
#define BOLD    "\033[1m"

static const char *answers[TOTAL_LEVELS] = {
    "goblin73",     /* Level 1 */
    "spider2099",   /* Level 2 */
    "y0ur_fr1end1y_n3igh8ourh0od_sp1derman"   /* Level 3 */
};

void print_banner(void)
{
    printf(CYAN BOLD);
    printf("\n");
    printf("========================================\n");
    printf("          GDG KIIT: COMMAND LINE        \n");
    printf("                 UTILITY                \n");
    printf("========================================\n");
    printf(RESET);
}

int main(int argc, char *argv[])
{
    print_banner();

    if (argc != 3) {
        printf(YELLOW);
        printf("\nUsage:\n");
        printf("    %s <level> <answer>\n", argv[0]);
        printf(RESET);

        printf("\nExample:\n");
        printf("    %s 1 bugman123\n\n", argv[0]);

        return 1;
    }

    int level = atoi(argv[1]);

    if (level < 1 || level > TOTAL_LEVELS) {
        printf(RED);
        printf("\n[!] Invalid level.\n");
        printf("[!] Valid levels: 1-%d\n\n", TOTAL_LEVELS);
        printf(RESET);

        return 1;
    }

    printf("\n");
    printf(CYAN "[*]" RESET " Checking submission...\n");
    printf(CYAN "[*]" RESET " Level: %d\n", level);
    printf(CYAN "[*]" RESET " Verifying answer...\n\n");

    if (strcmp(argv[2], answers[level - 1]) == 0) {
        printf(GREEN BOLD);
        printf("========================================\n");
        printf("              YAY YOU DID IT            \n");
        printf("========================================\n");
        printf(RESET);

        printf(GREEN "[+]" RESET " Correct answer.\n");
        printf(GREEN "[+]" RESET " Level %d completed.\n\n", level);
        return 0;
    }

    printf(RED BOLD);
    printf("========================================\n");
    printf("                 WRONG                  \n");
    printf("========================================\n");
    printf(RESET);

    printf(RED "[-]" RESET " Incorrect answer.\n");
    printf("[-] Level %d remains locked.\n\n", level);

    return 1;
}
